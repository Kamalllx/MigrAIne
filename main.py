import sys, json
from engine import run_pipeline

def main():
    tf_file = sys.argv[1] if len(sys.argv) > 1 else "sample_input.tf"
    
    print(f"🚀 MigrAI — GCP → AWS Migration Accelerator")
    print(f"   Input: {tf_file}\n{'─'*50}")

    with open(tf_file, "r") as f:
        gcp_terraform = f.read()

    results = run_pipeline(gcp_terraform)

    # Write outputs
    with open("output_cloudformation.yaml", "w") as f:
        f.write(results["cloudformation"])

    with open("output_runbook.md", "w") as f:
        f.write(results["runbook"])

    print(f"\n{'─'*50}")
    print(f"✅ Done in {results['cycles_used']} refinement cycle(s).")
    print(f"   📄 output_cloudformation.yaml")
    print(f"   📋 output_runbook.md")

if __name__ == "__main__":
    main()