"""Exercise a restricted native Key against the selected source copy, without network or database."""
import asyncio,importlib.util,json,sys
from datetime import datetime,timezone
from pathlib import Path
from dataclasses import dataclass

source=Path(sys.argv[1]).resolve()
name="litellm.proxy.management_endpoints.account_pool_cockpit_subject"
spec=importlib.util.spec_from_file_location(name,source)
module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
KEY="a"*64
row={"token":KEY,"blocked":None,"expires":None,"metadata":{"account_pool_card_id":"00000000-0000-0000-0000-000000000001"},"models":["scope-model"],"aliases":{},"config":{},"permissions":{},"router_settings":None,"access_group_ids":[],"policies":[],"allowed_routes":[],"allowed_cache_controls":[],"key_type":None,"model_max_budget":{},"budget_fallbacks":{},"budget_limits":None,"user_id":None,"team_id":None,"project_id":None,"organization_id":None,"agent_id":None,"object_permission_id":None,"budget_id":None,"max_budget":None,"budget_duration":None,"budget_reset_at":None,"user_scope":None,"team_scope":None,"project_scope":None,"membership_scope":None}
@dataclass(frozen=True)
class Repository:
    live:dict
    async def find_deleted(self,key_hash):return None
    async def find_live(self,key_hash):return self.live
def owner(name,models,team=None):
    return {"subject_id":name,"models":models,"blocked":False,"permissions_unrestricted":True,"team_id":team}
linked={**row,"models":["scope-model","excluded-model"],"user_id":"user-1","team_id":"team-1","project_id":"project-1","user_scope":owner("user-1",["personal-only"]),"team_scope":owner("team-1",["scope-model","team-only"]),"project_scope":owner("project-1",["scope-model"],"team-1"),"membership_scope":{"user_id":"user-1","team_id":"team-1","budget_id":None}}
async def run():
    results={}
    for label,live in [("key_models",row),("linked_models",linked)]:
        result=await module.inspect_native_subject_with_repository(KEY,Repository(live),now=datetime(2026,10,24,tzinfo=timezone.utc))
        results[label]=result.model_dump(mode="json")
    print(json.dumps(results,sort_keys=True))
asyncio.run(run())
