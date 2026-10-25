from pathlib import Path
import asyncio,importlib,importlib.util,json,sys
import httpx

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
policy=load("transaction_policy",sys.argv[1]);runtime=load("transaction_runtime",sys.argv[2]);admin=importlib.import_module(policy.CockpitAdminClient.__module__);assert policy.CockpitFailure is runtime.CockpitFailure
R1='"sha256-'+"a"*64+'"';R2='"sha256-'+"b"*64+'"'
async def main():
 calls=[];current={"revision":R1,"active":R1,"data":{"routingStrategy":"auto","accounts":[{"id":"account-a","authKind":"oauth"}],"apiKeys":[{"key":"FIXTURE-SECRET","enabled":True}],"customRoutingRules":[{"accountId":"account-a","priority":1,"futureRule":42}],"futureRoot":{"keep":True}}}
 def handle(request):
  calls.append(request.method+" "+request.url.path)
  if request.url.path.endswith("/manifest"):
   if request.method=="GET":return httpx.Response(200,json=current["data"],headers={"ETag":current["revision"]})
   assert request.headers["If-Match"]==R1;data=json.loads(request.content);assert data["apiKeys"]==current["data"]["apiKeys"] and data["futureRoot"]==current["data"]["futureRoot"] and data["customRoutingRules"][0]["futureRule"]==42
   current["data"]=data;current["revision"]=R2;return httpx.Response(202,json={"status":"persisted","restartRequired":True},headers={"ETag":R2})
  if request.url.path.endswith("/policy-apply"):
   assert request.method=="POST" and request.headers["If-Match"]==R2;current["active"]=R2
  return httpx.Response(200,json={"persistedRevision":current["revision"],"activeRevision":current["active"],"applySupported":True,"blockedReason":None})
 async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
  client=admin.CockpitAdminClient(admin.CockpitAdminConfig(url="http://cockpit.test",token="s"*32),http)
  before=await policy.read_cockpit_policy(client);saved=await policy.save_cockpit_policy(client,policy.CockpitPolicyUpdate(revision=R1,routing_strategy="custom",custom_rules=(policy.CockpitRoutingRule(account_id="account-a",priority=5),)))
  observed=await runtime.read_cockpit_runtime(client);applied=await runtime.apply_cockpit_policy(client,runtime.CockpitPolicyApply(revision=R2))
  assert "FIXTURE-SECRET" not in before.model_dump_json();assert saved.revision==R2 and observed.active_revision==R1 and applied.active_revision==R2
 failures=[]
 for status in (412,503):
  seen=[]
  def fail(request):
   seen.append(request.method);return httpx.Response(status,text="PRIVATE")
  async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
   failed=await runtime.apply_cockpit_policy(admin.CockpitAdminClient(admin.CockpitAdminConfig(url="http://cockpit.test",token="s"*32),http),runtime.CockpitPolicyApply(revision=R2))
  assert len(seen)==1;failures.append({"http_status":status,"status":failed.status,"code":failed.code,"calls":len(seen)})
 print(json.dumps({"policy_read":"auto","policy_saved":"custom","unknown_fields_preserved":True,"saved_not_activated":observed.active_revision!=observed.persisted_revision,"explicit_apply_matches_revision":applied.active_revision==R2,"calls":calls,"failures":failures},sort_keys=True))
asyncio.run(main())
