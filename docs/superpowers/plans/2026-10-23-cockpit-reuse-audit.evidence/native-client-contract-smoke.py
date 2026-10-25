from pathlib import Path
import asyncio,importlib.util,json,sys
import httpx
source=Path(sys.argv[1]);spec=importlib.util.spec_from_file_location("client_contract_target",source);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
async def main():
 revision='"sha256-'+"a"*64+'"';next_revision='"sha256-'+"b"*64+'"';payload={"accounts":[{"id":"fixture-account"}],"apiKeys":[{"enabled":True},{"enabled":False}],"future":{"keep":[1,None]}};seen=[]
 def handle(request):
  seen.append(request.method+" "+request.url.path)
  assert request.headers["authorization"]=="Bearer "+"s"*32
  if request.method=="GET":return httpx.Response(200,json=payload,headers={"ETag":revision})
  assert request.headers["if-match"]==revision and json.loads(request.content)==payload
  return httpx.Response(202,json={"status":"persisted","restartRequired":True},headers={"ETag":next_revision})
 async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
  client=module.CockpitAdminClient(module.CockpitAdminConfig(url="http://cockpit.test:8317",token="s"*32),http);snapshot=await client.read_manifest();assert isinstance(snapshot,module.ManifestSnapshot);written=await client.write_manifest(snapshot.data,snapshot.revision);assert isinstance(written,module.AdminReceipt)
 failures=[]
 for status in (302,412,503):
  calls=[]
  def fail(request):
   calls.append(request.method);return httpx.Response(status,text="SECRET-FIXTURE",headers={"Location":"http://private.invalid/secret"})
  async with httpx.AsyncClient(transport=httpx.MockTransport(fail),follow_redirects=True) as http:
   value=await module.CockpitAdminClient(module.CockpitAdminConfig(url="http://cockpit.test:8317",token="s"*32),http).read_manifest()
  assert isinstance(value,module.CockpitFailure) and len(calls)==1 and "SECRET" not in repr(value)
  failures.append({"status":status,"category":value.category,"calls":len(calls)})
 print(json.dumps({"account_count":snapshot.account_count,"enabled_key_count":snapshot.enabled_key_count,"unknown_fields_preserved":snapshot.data==payload,"write_status":written.status,"revision_matched":written.revision==next_revision,"requests":seen,"failures":failures},sort_keys=True))
asyncio.run(main())
