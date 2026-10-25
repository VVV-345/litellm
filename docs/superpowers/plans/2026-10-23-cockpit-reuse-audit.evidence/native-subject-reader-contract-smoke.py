from pathlib import Path
import asyncio,importlib.util,json,os,sys
from datetime import datetime,timezone
from uuid import UUID
from litellm.proxy.management_endpoints.account_pool_cockpit_subject import NativeSubjectResult,NativeModelScope
spec=importlib.util.spec_from_file_location('subject_transaction',sys.argv[1]);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
KEY='a'*64;CARD=UUID('e515c2db-08dc-443c-b85a-7936d2d42f2f');NOW=datetime(2026,10,23,tzinfo=timezone.utc)
async def main():
 inspections=[];requests=[];current=['restricted'];results={}
 async def inspect(key):
  assert key==KEY;inspections.append(current[0])
  if current[0]=='exception':raise RuntimeError('FIXTURE-PRIVATE-ERROR')
  if current[0]=='revoked':return NativeSubjectResult(status='revoked',checked_at=NOW)
  models=() if current[0]=='unrestricted' else ('alpha','alpha') if current[0]=='duplicate' else ('alpha','beta')
  return NativeSubjectResult(status='verified',card_id=CARD,model_scope=NativeModelScope(kind='unrestricted' if not models else 'restricted',models=models),checked_at=NOW)
 http=None
 if hasattr(module,'NativeTrustedSubjectReader'):
  reader=module.NativeTrustedSubjectReader(inspect)
 else:
  import httpx
  from fastapi import FastAPI
  from litellm.proxy.management_endpoints.account_pool_cockpit_receipts import create_internal_native_subject_router
  os.environ['ACCOUNT_POOL_MANAGER_TOKEN']='s'*32
  app=FastAPI();app.include_router(create_internal_native_subject_router(inspect),prefix='/account_pool')
  async def on_request(request):requests.append(request.url.path)
  http=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),event_hooks={'request':[on_request]})
  reader=module.CockpitSubjectClient(module.CockpitSubjectConfig(url='http://fixture.test',token='s'*32),http)
 try:
  for kind in ('restricted','unrestricted','revoked','duplicate','exception'):
   current[0]=kind;value=await reader.read(KEY);results[kind]=None if value is None else {'card_id':str(value.card_id),'model_scope':value.model_scope,'models':list(value.models)}
  results['invalid_hash']=await reader.read('sk-fixture-invalid')
 finally:
  if http is not None:await http.aclose()
 assert results['restricted']=={'card_id':str(CARD),'model_scope':'restricted','models':['alpha','beta']}
 assert results['unrestricted']=={'card_id':str(CARD),'model_scope':'unrestricted','models':[]}
 assert all(results[k] is None for k in ('revoked','duplicate','exception','invalid_hash'))
 assert len(inspections)==5
 print(json.dumps({'results':results,'inspector_calls':len(inspections),'http_calls':len(requests)},sort_keys=True))
asyncio.run(main())
