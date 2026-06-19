# main.py (your migration app)
from gcp_discovery import GCPDiscoveryClient

def main():
    # Initialize using ADC and default project resolution.
    client = GCPDiscoveryClient()
    
    # Get ALL raw resources from GCP
    print("Discovering all resources...")
    raw_assets = client.discover_all()
    
    print(f"Found {len(raw_assets)} raw assets")
    print("\nFirst asset structure:")
    print(raw_assets[0])  # See actual GCP data structure
    
    # Normalize to usable objects
    resources = client.normalize(raw_assets)
    
    # Filter by category
    compute_resources = [r for r in resources if r.category == 'compute']
    storage_resources = [r for r in resources if r.category == 'storage']
    
    print(f"\nCompute: {len(compute_resources)}")
    print(f"Storage: {len(storage_resources)}")
    
    # Access specific resource details
    for vm in compute_resources:
        print(f"\nVM: {vm.name}")
        print(f"  Type: {vm.config.get('machineType')}")
        print(f"  Zone: {vm.location}")
        print(f"  Labels: {vm.labels}")
        print(f"  IAM: {vm.iam_policy}")
        print(f"  Raw GCP data available: {list(vm.raw.keys())}")

if __name__ == "__main__":
    main()