"""
Additional parsers for specific GCP resource types.
Extract useful info from raw 'data' field.
"""


class ComputeParser:
    @staticmethod
    def parse_instance(data: dict) -> dict:
        """Extract VM details from compute instance data."""
        return {
            'machine_type': data.get('machineType', '').split('/')[-1],
            'cpu_platform': data.get('cpuPlatform'),
            'status': data.get('status'),
            'zone': data.get('zone', '').split('/')[-1],
            'network_interfaces': [
                {
                    'network': ni.get('network', '').split('/')[-1],
                    'subnetwork': ni.get('subnetwork', '').split('/')[-1],
                    'internal_ip': ni.get('networkIP'),
                    'external_ip': ni.get('accessConfigs', [{}])[0].get('natIP')
                }
                for ni in data.get('networkInterfaces', [])
            ],
            'disks': [
                {
                    'boot': d.get('boot', False),
                    'source': d.get('source', '').split('/')[-1],
                    'mode': d.get('mode', 'READ_WRITE')
                }
                for d in data.get('disks', [])
            ],
            'metadata': {
                item['key']: item.get('value', '')
                for item in data.get('metadata', {}).get('items', [])
            },
            'service_accounts': [
                sa.get('email')
                for sa in data.get('serviceAccounts', [])
            ],
            'tags': data.get('tags', {}).get('items', [])
        }


class StorageParser:
    @staticmethod
    def parse_bucket(data: dict) -> dict:
        """Extract storage bucket details."""
        return {
            'location': data.get('location'),
            'storage_class': data.get('storageClass'),
            'versioning_enabled': data.get('versioning', {}).get('enabled', False),
            'lifecycle_rules': data.get('lifecycle', {}).get