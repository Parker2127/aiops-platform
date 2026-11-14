"""Submit ml/train.py as a SageMaker script-mode training job (plain boto3, no SDK).

    python ml/train_sagemaker.py --upload-data --wait --pin

1. tars train.py + features.py + requirements.txt -> s3://<bucket>/code/
2. (--upload-data) uploads ml/data/train/*.jsonl -> s3://<bucket>/data/train/
3. CreateTrainingJob on the scikit-learn framework image (managed spot by default)
4. (--wait) polls to completion and prints the model artifact URI
5. (--pin) writes that URI into apps/log-detector/k8s/kustomization.yaml, so
   promoting the model is a git commit that Argo CD rolls out.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path

import boto3

HERE = Path(__file__).parent
ROOT = HERE.parent
KUSTOMIZATION = ROOT / "apps" / "log-detector" / "k8s" / "kustomization.yaml"
SOURCE_FILES = ("train.py", "features.py", "requirements.txt")

# AWS-owned registry for the SageMaker scikit-learn framework images, per region.
SKLEARN_REGISTRY = {"us-east-1": "683313688378", "us-east-2": "257758044811", "us-west-2": "246618743249"}
SKLEARN_TAG = "1.9-0-cpu-py3"


def source_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in SOURCE_FILES:
            tar.add(HERE / name, arcname=name)
    return buf.getvalue()


def pin_model(uri: str) -> None:
    text = KUSTOMIZATION.read_text()
    new, n = re.subn(r"MODEL_S3_URI=\S+", f"MODEL_S3_URI={uri}", text)
    if n != 1:
        raise SystemExit(f"could not find MODEL_S3_URI=... in {KUSTOMIZATION}")
    KUSTOMIZATION.write_text(new)
    print(f"pinned {uri} in {KUSTOMIZATION.relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> None:
    session = boto3.session.Session()
    region = session.region_name or "us-east-1"
    account = session.client("sts").get_caller_identity()["Account"]

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bucket", default=f"aiops-artifacts-{account}")
    p.add_argument("--role-arn", default=f"arn:aws:iam::{account}:role/aiops-dev-sagemaker")
    p.add_argument("--data-dir", type=Path, default=HERE / "data" / "train")
    p.add_argument("--upload-data", action="store_true")
    p.add_argument("--instance-type", default="ml.m5.large")
    p.add_argument("--no-spot", dest="spot", action="store_false", help="on-demand instead of managed spot")
    p.add_argument("--image-uri", default=None)
    p.add_argument("--n-estimators", type=int, default=200)
    p.add_argument("--contamination", type=float, default=0.05)
    p.add_argument("--wait", action="store_true")
    p.add_argument("--pin", action="store_true", help="pin the model URI in the detector's kustomization")
    args = p.parse_args(argv)

    if args.pin and not args.wait:
        p.error("--pin requires --wait")

    s3 = session.client("s3")
    sm = session.client("sagemaker")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    job_name = f"aiops-log-anomaly-{stamp}"

    code_key = f"code/{job_name}/sourcedir.tar.gz"
    s3.put_object(Bucket=args.bucket, Key=code_key, Body=source_tarball())
    print(f"uploaded source -> s3://{args.bucket}/{code_key}")

    if args.upload_data:
        files = sorted(args.data_dir.glob("*.jsonl"))
        if not files:
            raise SystemExit(f"no data in {args.data_dir}; run python ml/generate_logs.py")
        for f in files:
            s3.upload_file(str(f), args.bucket, f"data/train/{f.name}")
            print(f"uploaded {f.name} -> s3://{args.bucket}/data/train/")

    image = (
        args.image_uri
        or f"{SKLEARN_REGISTRY[region]}.dkr.ecr.{region}.amazonaws.com/sagemaker-scikit-learn:{SKLEARN_TAG}"
    )

    # Framework containers expect every hyperparameter JSON-encoded.
    hyperparameters = {
        "sagemaker_program": "train.py",
        "sagemaker_submit_directory": f"s3://{args.bucket}/{code_key}",
        "sagemaker_region": region,
        "n-estimators": args.n_estimators,
        "contamination": args.contamination,
    }

    request = {
        "TrainingJobName": job_name,
        "RoleArn": args.role_arn,
        "AlgorithmSpecification": {
            "TrainingImage": image,
            "TrainingInputMode": "File",
            "MetricDefinitions": [
                {"Name": m, "Regex": rf"{m}=([0-9\.]+);"}
                for m in ("precision", "recall", "f1", "flagged_rate")
            ],
        },
        "HyperParameters": {k: json.dumps(v) for k, v in hyperparameters.items()},
        "InputDataConfig": [
            {
                "ChannelName": "train",
                "DataSource": {
                    "S3DataSource": {
                        "S3DataType": "S3Prefix",
                        "S3Uri": f"s3://{args.bucket}/data/train/",
                        "S3DataDistributionType": "FullyReplicated",
                    }
                },
            }
        ],
        "OutputDataConfig": {"S3OutputPath": f"s3://{args.bucket}/models/"},
        "ResourceConfig": {"InstanceType": args.instance_type, "InstanceCount": 1, "VolumeSizeInGB": 10},
        "StoppingCondition": {"MaxRuntimeInSeconds": 1800},
        "Tags": [{"Key": "Project", "Value": "aiops-platform"}],
    }
    if args.spot:
        request["EnableManagedSpotTraining"] = True
        request["StoppingCondition"]["MaxWaitTimeInSeconds"] = 3600

    sm.create_training_job(**request)
    print(f"started training job {job_name}")
    print(f"console: https://{region}.console.aws.amazon.com/sagemaker/home?region={region}#/jobs/{job_name}")

    if not args.wait:
        return

    while True:
        desc = sm.describe_training_job(TrainingJobName=job_name)
        status = desc["TrainingJobStatus"]
        print(f"  {datetime.now(UTC):%H:%M:%S} {status} / {desc.get('SecondaryStatus')}")
        if status in ("Completed", "Failed", "Stopped"):
            break
        time.sleep(30)

    if status != "Completed":
        print(f"job ended {status}: {desc.get('FailureReason', '')}", file=sys.stderr)
        raise SystemExit(1)

    for metric in desc.get("FinalMetricDataList", []):
        print(f"  {metric['MetricName']}: {metric['Value']:.4f}")
    if desc.get("BillableTimeInSeconds"):
        print(f"  billable seconds: {desc['BillableTimeInSeconds']}")

    uri = desc["ModelArtifacts"]["S3ModelArtifacts"]
    print(f"model artifact: {uri}")
    if args.pin:
        pin_model(uri)
        print("commit + push the kustomization change to ship the model via Argo CD")


if __name__ == "__main__":
    main()
