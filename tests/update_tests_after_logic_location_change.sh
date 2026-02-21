#!/usr/bin/env bash
#
# update_tests_after_logic_location_change.sh
#
# Reads business_config_uri from local-config.yaml, fetches business-config.yaml
# from that location, then updates all hardcoded "$schema" values in test files
# to match the canonical URLs defined in schema_to_helper_mapping.
#
# Run this whenever the logic repo moves to a new location.
# Usage: ./tests/update_tests_after_logic_location_change.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_CONFIG="$SCRIPT_DIR/../validation_lib/local-config.yaml"

python3 - "$SCRIPT_DIR" "$LOCAL_CONFIG" << 'PYEOF'
import sys, os, re, yaml, urllib.request

test_dir, local_config_path = sys.argv[1], sys.argv[2]

# ── 1. Read business_config_uri from local-config.yaml ───────────────────────
with open(local_config_path) as f:
    local_config = yaml.safe_load(f)

business_config_uri = local_config.get('business_config_uri', '')
if not business_config_uri:
    print(f"Error: business_config_uri not found in {local_config_path}", file=sys.stderr)
    sys.exit(1)

print(f"business_config_uri: {business_config_uri}")

# ── 2. Fetch or read business-config.yaml ────────────────────────────────────
if business_config_uri.startswith('http'):
    with urllib.request.urlopen(business_config_uri) as r:
        business_config = yaml.safe_load(r.read())
else:
    # Resolve relative path from the local-config.yaml's directory
    base = os.path.dirname(os.path.abspath(local_config_path))
    path = os.path.normpath(os.path.join(base, business_config_uri))
    with open(path) as f:
        business_config = yaml.safe_load(f)

# ── 3. Extract canonical schema URLs from schema_to_helper_mapping ────────────
canonical_urls = list(business_config.get('schema_to_helper_mapping', {}).keys())
if not canonical_urls:
    print("Error: no schema URLs found in schema_to_helper_mapping", file=sys.stderr)
    sys.exit(1)

print(f"\nCanonical schema URLs ({len(canonical_urls)}):")
for u in canonical_urls:
    print(f"  {u}")
print()

def extract_entity_version(url):
    """
    Extract (entity_type, version) from a schema URL.

    Handles two formats:
      - Filename:  .../models/loan.schema.v1.0.0.json  -> ("loan", "v1.0.0")
      - Path-based: .../schemas/loan/v1.0.0            -> ("loan", "v1.0.0")
    """
    path = url.rstrip('/').split('?')[0]
    filename = path.split('/')[-1]

    if filename.endswith('.json'):
        # e.g. "loan.schema.v1.0.0.json" -> split on ".schema."
        stem = filename[:-5]  # "loan.schema.v1.0.0"
        if '.schema.' in stem:
            entity, version_part = stem.split('.schema.', 1)
            return entity, version_part  # ("loan", "v1.0.0")

    # Path-based: look for a version segment (/v1.0.0) in the URL path
    segments = [s for s in path.split('/') if s]
    for i, seg in enumerate(segments):
        if re.match(r'^v\d+\.\d+', seg):
            entity = segments[i - 1] if i > 0 else None
            return entity, seg  # ("loan", "v1.0.0")

    return None, None

# Build lookup: multiple identifiers -> canonical URL
sig_map = {}
for url in canonical_urls:
    sig_map[url] = url                          # exact URL match

    filename = url.rstrip('/').split('?')[0].split('/')[-1]
    if filename.endswith('.json'):
        sig_map[filename] = url                 # e.g. "loan.schema.v1.0.0.json"
        sig_map[filename[:-5]] = url            # e.g. "loan.schema.v1.0.0"

    entity, version = extract_entity_version(url)
    if entity and version:
        sig_map[f"{entity}|{version}"] = url   # e.g. "loan|v1.0.0"

# ── 4. Find and update test files ─────────────────────────────────────────────
schema_pattern = re.compile(r'("\$schema"\s*:\s*")([^"]+)(")')

test_files = sorted(
    os.path.join(test_dir, f)
    for f in os.listdir(test_dir)
    if os.path.isfile(os.path.join(test_dir, f)) and f.endswith(('.py', '.json'))
)

for filepath in test_files:
    with open(filepath) as f:
        content = f.read()

    replacements = []

    def replace_schema(m, _replacements=replacements):
        prefix, old_url, suffix = m.group(1), m.group(2), m.group(3)

        # 1. Exact or already-canonical match
        if old_url in sig_map:
            new_url = sig_map[old_url]
            if new_url != old_url:
                _replacements.append((old_url, new_url))
            return prefix + new_url + suffix

        # 2. Filename-based match (handles repo/branch moves)
        filename = old_url.rstrip('/').split('?')[0].split('/')[-1]
        if filename in sig_map:
            new_url = sig_map[filename]
            _replacements.append((old_url, new_url))
            return prefix + new_url + suffix

        # 3. Entity + version match (handles URL style changes, e.g. bank.example.com)
        entity, version = extract_entity_version(old_url)
        if entity and version:
            key = f"{entity}|{version}"
            if key in sig_map:
                new_url = sig_map[key]
                _replacements.append((old_url, new_url))
                return prefix + new_url + suffix

        print(f"  WARNING: no canonical match for $schema: {old_url}")
        return m.group(0)

    new_content = schema_pattern.sub(replace_schema, content)

    if new_content != content:
        with open(filepath, 'w') as f:
            f.write(new_content)
        print(f"Updated: {os.path.basename(filepath)}")
        for old, new in replacements:
            print(f"  - {old}")
            print(f"  + {new}")
    else:
        print(f"No change: {os.path.basename(filepath)}")

print("\nDone.")
PYEOF
