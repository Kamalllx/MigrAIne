from pathlib import Path

from gcp_discovery import GCPDiscoveryClient


def main() -> None:
    client = GCPDiscoveryClient()
    terraform_text = client.build_terraform_file()

    output_path = Path("gcp_architecture.tf")
    output_path.write_text(terraform_text, encoding="utf-8")

    print(f"Saved Terraform configuration to: {output_path.resolve()}")


if __name__ == "__main__":
    main()
