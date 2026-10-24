"""Exercise the selected receipt implementation with fake in-memory/HTTP transport fixtures."""
import asyncio,importlib.util,json,sys
from pathlib import Path
root=Path.cwd();tree=Path(sys.argv[1]).resolve();source=tree/"account-pool/account_pool/cockpit_credential_receipts.py"
if not source.exists():
    print(json.dumps({"status":"receipt_service_unavailable","ready_to_migrate":False},sort_keys=True));sys.exit(0)
sys.path.insert(0,str(root/"account-pool/tests"))
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value);return value
for name,relative in [("account_pool.shared.secrets","shared/secrets.py"),("account_pool.cockpit_admin","cockpit_admin.py"),("account_pool.cockpit_preflight","cockpit_preflight.py"),("account_pool.cockpit_credential_receipts","cockpit_credential_receipts.py")]:
    module(name,tree/"account-pool/account_pool"/relative)
fixtures=module("receipt_fixture",tree/"account-pool/tests/account_pool/test_cockpit_credential_receipts.py")
async def main():
    async with fixtures.harness() as (service,readers,ownership):
        issued=await service.issue(fixtures.SUBJECT)
        assert issued.status=="owned_artifact_correspondence",issued
        encoded=issued.model_dump_json();fresh=await service.revalidate(fixtures.SUBJECT,encoded)
        changed=json.loads(encoded);sig=changed["signature"];changed["signature"]=("0" if sig[0]!="0" else "1")+sig[1:]
        tamper=await service.revalidate(fixtures.SUBJECT,json.dumps(changed))
        readers.records=(readers.records[0].model_copy(update={"version":4}),)
        stale=await service.revalidate(fixtures.SUBJECT,encoded)
        readers.records=(readers.records[0].model_copy(update={"version":3}),)
        readers.accounts[0]["AUTHID"]="other.json"
        case_alias=await service.issue(fixtures.SUBJECT)
        print(json.dumps({"case_alias":case_alias.code,"issue":issued.status,"revalidate":fresh.status,"tamper":tamper.code,"stale":stale.code,"models":list(issued.payload.bindings.scope.models),"ready_to_migrate":issued.ready_to_migrate,"credential_material_exposed":"FAKE" in encoded,"http_methods":sorted({x.split()[0] for x in readers.calls})},sort_keys=True))
asyncio.run(main())
