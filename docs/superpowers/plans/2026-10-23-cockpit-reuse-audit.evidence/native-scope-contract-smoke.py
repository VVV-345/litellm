import asyncio,hashlib,importlib,importlib.util,json,sys
from functools import partial
from uuid import UUID
import httpx
from pydantic import SecretStr
spec=importlib.util.spec_from_file_location('scope_transaction',sys.argv[1]);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
c=importlib.import_module(m.CredentialReceipt.__module__);admin=importlib.import_module(m.CockpitAdminClient.__module__)
SUBJECT='a'*64;CARD=UUID(int=1);SEED='FAKE-SEED-ONLY-32-CHARACTERS'
async def main():
 calls=[];subject_calls=[];state={'revision':'"sha256-'+'b'*64+'"','mode':'ok','revoked':False,'reject':False,'data':{'accounts':[{'id':str(CARD),'authId':'target.json','authKind':'oauth'}],'apiKeys':[{'id':'private','enabled':True,'key':'PRIVATE','accountIds':[str(CARD)]}],'modelIds':['fixture-model'],'futureRoot':{'keep':True}}}
 def scope():return c.TrustedCredentialScope(subject_hash=SUBJECT,card_id=CARD,model_scope='restricted',models=('fixture-model',))
 class Receipts:
  async def revalidate(self,subject_hash,encoded):
   assert subject_hash==SUBJECT and encoded=='encoded'
   if state['reject']:return c.CredentialReceiptRejection('stale_receipt')
   bindings=c.CredentialBindings(scope=scope(),source_version=1,source_file='source.json',source_state_digest='b'*64,source_fingerprint_digest='c'*64,source_content_digest='d'*64,source_size=1,target_account_id=CARD,target_auth_id='target.json',target_file_revision='"sha256-'+'d'*64+'"',manifest_revision=state['revision'])
   return c.CredentialReceipt(payload=c.CredentialReceiptPayload(issued_at=1000,expires_at=1300,bindings=bindings),signature='e'*64)
 class Subjects:
  async def read(self,subject_hash):
   assert subject_hash==SUBJECT;subject_calls.append(subject_hash);return None if state['revoked'] else scope()
 def handle(request):
  assert request.url.path=='/admin/v1/manifest';calls.append(request.method)
  if request.method=='GET':return httpx.Response(200,json=state['data'],headers={'ETag':state['revision']})
  assert request.method=='PUT' and request.headers['If-Match']==state['revision']
  if state['mode']=='conflict':return httpx.Response(412,text='PRIVATE')
  if state['mode']=='lost':raise httpx.ReadTimeout('PRIVATE',request=request)
  state['data']=json.loads(request.content);state['revision']='"sha256-'+hashlib.sha256(request.content).hexdigest()+'"'
  return httpx.Response(202,json={'status':'persisted','restartRequired':True},headers={'ETag':state['revision']})
 async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
  seed=m.EnvironmentSecretDeriver(SEED) if hasattr(m,'EnvironmentSecretDeriver') else SecretStr(SEED)
  token=m.subject_routing_token(seed,SUBJECT);assert token=='60KMmWTxYqLThx_ZncaYJjQa1YqW7tC3Rq47l3nk7-o'
  dependency=seed if hasattr(m,'EnvironmentSecretDeriver') else partial(m.subject_routing_token,seed)
  service=m.CockpitScopeService(Receipts(),admin.CockpitAdminClient(admin.CockpitAdminConfig(url='http://cockpit.test',token=SecretStr('s'*32)),http),dependency,'http://cockpit-data.test',Subjects())
  views=[await service.read(SUBJECT)]
  state['reject']=True;before=len(calls);rejected=await service.publish(m.CockpitScopePublishRequest(subject_hash=SUBJECT,receipt='encoded',revision=state['revision']));assert (rejected.status,rejected.code)==(409,'receipt_stale') and len(calls)==before;state['reject']=False
  views.append(await service.publish(m.CockpitScopePublishRequest(subject_hash=SUBJECT,receipt='encoded',revision=state['revision'])))
  assert state['data']['apiKeys'][-1]['enabled'] is False and token not in views[-1].model_dump_json()
  views.append(await service.route_state(SUBJECT));views.append(await service.activate(m.CockpitScopeActivationRequest(subject_hash=SUBJECT,revision=state['revision'])))
  active=await service.route_state(SUBJECT);assert active.api_key==token and token not in repr(active);views.append(active)
  state['revoked']=True;blocked=await service.route_state(SUBJECT);assert blocked.api_key is None;views.append(blocked)
  views.append(await service.deactivate(m.CockpitScopeActivationRequest(subject_hash=SUBJECT,revision=state['revision'])))
  views.append(await service.revoke(m.CockpitScopeRevokeRequest(subject_hash=SUBJECT,revision=state['revision'])))
  views.append(await service.read(SUBJECT))
  assert state['data']['apiKeys']==[{'id':'private','enabled':True,'key':'PRIVATE','accountIds':[str(CARD)]}] and state['data']['futureRoot']=={'keep':True}
  failures=[]
  for mode,want in [('conflict',(409,'revision_conflict')),('lost',(503,'write_outcome_unknown'))]:
   state['mode']=mode;before=len(calls);failed=await service.publish(m.CockpitScopePublishRequest(subject_hash=SUBJECT,receipt='encoded',revision=state['revision']));assert (failed.status,failed.code)==want and calls[before:]==['GET','PUT'];failures.append({'status':failed.status,'code':failed.code,'put_attempts':1})
  states=[v.state for v in views];assert states==['absent','staged','staged','active','active','blocked','staged','absent','absent'];assert len(subject_calls)==3
  assert all(v.runtime_activation=='restart_required_or_unverified' for v in views)
 print(json.dumps({'states':states,'subject_reads':len(subject_calls),'stale_receipt_writes':0,'unknown_fields_preserved':True,'remaining_keys':1,'runtime_activation':'restart_required_or_unverified','calls':calls,'failures':failures},sort_keys=True))
asyncio.run(main())
