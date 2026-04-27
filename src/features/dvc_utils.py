import glob
import logging
import os
import shutil
import subprocess


def pull_raw_data(dvc_file, local_csv, remote, project_root):
    """
    Returns "pulled" or "skipped". Raises RuntimeError if neither succeeds.
    Pure Python — no Airflow imports.
    """
    user = os.environ.get("DAGSHUB_USER")
    token = os.environ.get("DAGSHUB_TOKEN")

    if user and token:
        subprocess.run(
            ["dvc", "remote", "modify", remote, "--local", "user", user],
            cwd=project_root, check=True,
        )
        subprocess.run(
            ["dvc", "remote", "modify", remote, "--local", "password", token],
            cwd=project_root, check=True,
        )

    try:
        subprocess.run(
            ["dvc", "pull", dvc_file, "--remote", remote],
            cwd=project_root, check=True,
        )
        published_dir = os.path.join(project_root, "data", "published")
        csv_files = glob.glob(os.path.join(published_dir, "**", "*.csv"), recursive=True)
        if not csv_files:
            raise RuntimeError(f"DVC pull succeeded but no .csv found in {published_dir}")
        shutil.copy(csv_files[0], local_csv)
        logging.info("DVC pull succeeded — copied %s → %s", csv_files[0], local_csv)
        return "pulled"

    except subprocess.CalledProcessError as e:
        logging.warning("DVC pull failed: %s", e)
        if os.path.exists(local_csv):
            logging.warning("Falling back to existing local file: %s", local_csv)
            return "skipped"
        raise RuntimeError(
            f"DVC pull failed AND no local file at {local_csv}. Cannot proceed."
        )
