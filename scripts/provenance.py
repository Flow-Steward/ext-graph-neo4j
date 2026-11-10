"""Record exact source, patches, toolchain and both native payload digests."""
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
info=json.loads((root/'scripts/build-config.json').read_text())
if info.get('patch'):info['patch_sha256']=digest(root/info['patch'])
if 'driver' in info and info['driver'].get('patch'):info['driver']['patch_sha256']=digest(root/info['driver']['patch'])
if (root/'native/main.go').exists():info['source_sha256']=digest(root/'native/main.go')
info['approved_profiles']={'patch':'scripts/patches/0000-approved-prebuilt-profiles.patch','sha256':digest(root/'scripts/patches/0000-approved-prebuilt-profiles.patch')}
info['binaries']={}
for target in ('linux-amd64','linux-arm64'):
 p=root/'bin'/target/'toolbox';info['binaries'][target]={'sha256':digest(p),'size':p.stat().st_size}
(root/'toolbox-provenance.json').write_text(json.dumps(info,indent=2)+'\n')

driver=info['driver'].copy()
driver['upstream_module']={'ecosystem':'Go','name':'github.com/neo4j/neo4j-go-driver/v6','version':'v6.4.0'}
driver.update({'upstream_tag':driver['version'],'module':'github.com/neo4j/neo4j-go-driver/v6','module_version':driver['version'],'upstream_module_sum':driver['module_sum'],'patch_sha256':digest(root/driver['patch']),'toolbox_source_commit':info['commit']})
(root/'neo4j-driver-provenance.json').write_text(json.dumps(driver,indent=2)+'\n')
