(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  function savedToken(){
    try{return localStorage.getItem('remote-token')||sessionStorage.getItem('remote-token')||'';}
    catch{return sessionStorage.getItem('remote-token')||'';}
  }
  function rememberToken(token){
    sessionStorage.setItem('remote-token',token);
    try{localStorage.setItem('remote-token',token);}catch{}
  }
  function forgetToken(){
    sessionStorage.removeItem('remote-token');
    try{localStorage.removeItem('remote-token');}catch{}
  }
  let initialToken=savedToken();
  if(globalThis.location?.hash){
    const scanned=new URLSearchParams(location.hash.slice(1)).get('connect');
    if(scanned && /^[A-Za-z0-9_-]{32,128}$/.test(scanned)){
      if(initialToken && scanned!==initialToken)sessionStorage.removeItem('remote-pending');
      initialToken=scanned;
      sessionStorage.setItem('remote-token',scanned);
    }
    // Fragments never go to the server. Remove the credential from the address bar immediately.
    if(scanned)history.replaceState(null,'',location.pathname+location.search);
  }
  let pending=null;
  try{pending=JSON.parse(sessionStorage.getItem('remote-pending')||'null');}catch{sessionStorage.removeItem('remote-pending');}
  const state = {token:initialToken, tab:'sessions', sessions:[], nextCursor:null, previewUrl:null, refreshing:false, zoom:1, online:false, serverInstance:null, pending};
  const toast = (message) => {
    $('toast').textContent = message;
    $('toast').classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => $('toast').classList.remove('show'), 4200);
  };
  async function api(path, data, verb) {
    const opts = {method:verb || (data===undefined?'GET':'POST'),headers:{'X-Remote-Token':state.token}};
    if(data!==undefined){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(data);}
    let response;
    const controller=new AbortController();opts.signal=controller.signal;
    const timer=setTimeout(()=>controller.abort(),15000);
    try { response = await fetch('/api/'+path,opts); }
    catch(e){throw Error(e.name==='AbortError'?'서버 응답이 늦어요. 잠시 후 다시 확인할게요.':'노트북 서버에 접속하지 못했어요. Tailscale 연결과 노트북 실행 창을 확인해 주세요.');}
    finally{clearTimeout(timer);}
    if(!response.ok){
      let detail='HTTP '+response.status;
      try { detail=(await response.json()).detail || detail; } catch{}
      if(response.status===401)detail='저장된 연결을 사용할 수 없어요. 노트북에 표시된 QR 코드를 다시 스캔해 주세요.';
      const error=Error(detail);error.status=response.status;throw error;
    }
    return response;
  }
  const data = async (path,payload) => (await api(path,payload)).json();
  function showPaired(yes){$('pairing').hidden=yes;$('workspace').hidden=!yes;$('composer').hidden=!yes;$('pendingPanel').hidden=!yes||!state.pending;}
  function savePending(value){
    state.pending=value;
    if(value)sessionStorage.setItem('remote-pending',JSON.stringify(value));
    else sessionStorage.removeItem('remote-pending');
    $('pendingPanel').hidden=!value||!state.token;
    $('pendingNote').textContent='전송 결과가 아직 확인되지 않았습니다. 확인 전에는 같은 메시지를 새로 보내지 마세요.';
  }
  let statusRequest=null;
  function status(){
    if(statusRequest)return statusRequest;
    statusRequest=refreshStatus().finally(()=>{statusRequest=null;});
    return statusRequest;
  }
  async function refreshStatus(){
    try{
      const s=await data('status');
      rememberToken(state.token);
      const reconnect=!state.online;state.online=true;state.serverInstance=s.server_instance;
      $('connection').textContent = s.connected ? ('● 노트북 연결됨'+(s.minimized?' · 창 최소화됨':'')+(s.sleep_prevention_active?' · 절전 방지 중':'')) : ('● 서버 연결됨 · '+(s.notice||'ChatGPT 앱을 열어 주세요.'));
      $('connection').title=s.notice||'';
      if(reconnect)listSessions();
      return s;
    } catch(e){
      state.online=false;$('connection').textContent=e.status?'서버 응답 오류 ('+e.status+')':e.message;
      if(e.status===401){forgetToken();state.token='';showPaired(false);$('pairError').textContent=e.message;}
      return null;
    }
  }
  async function pair(){
    const nextToken=$('pairToken').value.trim();
    if(state.token && nextToken!==state.token && state.pending){toast('먼저 이전 전송 결과를 확인해 주세요.');return;}
    state.token=nextToken;
    try{
      await data('status');
      rememberToken(state.token);
      showPaired(true);$('pairError').textContent='';
      await Promise.all([status(),listSessions()]);
    } catch(e){$('pairError').textContent=e.message;showPaired(false);}
  }
  let readingSessions=false;
  async function listSessions(more=false){
    if(readingSessions)return;
    readingSessions=true;
    try{
      const cursor=more && state.nextCursor?'?cursor='+encodeURIComponent(state.nextCursor):'';
      const result=await data('sessions'+cursor);
      const combined=more?[...state.sessions,...(result.items||[])]:result.items||[];
      state.sessions=[...new Map(combined.map(item=>[item.id,item])).values()];
      state.nextCursor=result.next_cursor||null;
      $('moreSessions').hidden=!state.nextCursor;
      $('sessionNote').textContent=(result.note||'')+' · '+state.sessions.length+'개 불러옴';
      renderSessions();
    } catch(e){$('sessionNote').textContent=e.message;}
    finally{readingSessions=false;}
  }
  function renderSessions(){
    const list=$('sessionList');list.replaceChildren();
    const term=$('sessionSearch').value.trim().toLowerCase();
    const items=state.sessions.filter(s=>s.title.toLowerCase().includes(term));
    if(!items.length){const p=document.createElement('p');p.className='muted';p.textContent='항목을 읽지 못했습니다. 화면 탭에서 데스크톱 사이드바를 직접 조작할 수 있습니다.';list.append(p);return;}
    for(const s of items){
      const b=document.createElement('button');b.type='button';
      b.textContent=s.title;
      const tag=document.createElement('small');
      tag.textContent=(s.project_name||(s.project?'프로젝트 대화':'일반 대화'))+(s.updated_at?' · '+new Date(s.updated_at*1000).toLocaleString('ko-KR'): '');b.append(tag);
      b.addEventListener('click',async()=>{
        try{await data('sessions/select',{id:s.id});$('message').placeholder=s.title+'에 메시지 보내기';toast('대화를 열었어요: '+s.title);showTab('preview');await status();}
        catch(e){toast(e.message);}
      });
      list.append(b);
    }
  }
  async function loadPreview(){
    if(state.refreshing || state.tab!=='preview' || document.hidden)return;
    state.refreshing=true;
    try{
      const blob=await (await api('preview')).blob();
      const url=URL.createObjectURL(blob);
      $('previewImage').src=url;
      $('previewPlaceholder').hidden=true;
      if(state.previewUrl)URL.revokeObjectURL(state.previewUrl);
      state.previewUrl=url;
    }catch(e){$('previewPlaceholder').hidden=false;$('previewPlaceholder').textContent=e.message;}
    finally{state.refreshing=false;}
  }
  function showTab(tab){
    state.tab=tab;
    $('showSessions').classList.toggle('active',tab==='sessions');
    $('showPreview').classList.toggle('active',tab==='preview');
    $('sessionsPane').hidden=(tab!=='sessions');
    $('previewPane').hidden=(tab!=='preview');
    if(tab==='preview')loadPreview();
  }
  async function send(ev){
    ev.preventDefault();
    const text=$('message').value;
    if(!text.trim())return;
    $('sendButton').disabled=true;
    try{
      if(state.pending && state.pending.text!==text){toast('이전 전송 결과를 먼저 확인하거나 화면 확인 후 새로 작성을 누르세요.');return;}
      if(!state.pending){
        if(!await status())throw Error('연결이 복구되면 다시 보내세요.');
        const request_id = (globalThis.crypto?.randomUUID?.() || String(Date.now())+'_'+Math.random().toString(36).slice(2)).replace(/[^a-zA-Z0-9_-]/g,'');
        savePending({text,request_id,server_instance:state.serverInstance});
      }
      const result=await data('send',state.pending);
      if(result.input_sent){savePending(null);$('message').value='';toast('노트북에 키 입력을 전송했습니다.');}
      else toast(result.note||'전송이 확정되지 않았습니다. 노트북을 확인해 주세요.');
      if(state.tab==='preview')loadPreview();
    }catch(e){toast(e.message+' (중복 전송 방지를 위해 자동 재시도하지 않음)');}
    finally{$('sendButton').disabled=false;}
  }
  async function checkPending(){
    if(!state.pending)return;
    try{
      const s=await status();if(!s)throw Error('아직 연결되지 않았습니다.');
      if(s.server_instance!==state.pending.server_instance)throw Error('브리지가 재시작됐습니다. 화면에서 이전 메시지를 확인하세요.');
      const result=await data('sends/'+state.pending.request_id);
      if(result.input_sent){const text=state.pending.text;savePending(null);if($('message').value===text)$('message').value='';toast('노트북에 입력을 전달한 것으로 확인했습니다.');}
      else toast(result.note||'화면에서 전송 결과를 확인하세요.');
    }catch(e){toast(e.message);}
  }
  async function remoteScroll(amount,sidebar=false){
    try{await data('scroll',{amount,sidebar});if(sidebar)await listSessions();else await loadPreview();}
    catch(e){toast(e.message);}
  }
  $('pairButton').addEventListener('click',pair);
  async function brightness(dimmed){
    try{const result=await data('brightness',{dimmed});toast(result.note);}
    catch(e){toast(e.message);}
  }
  $('dimLaptop').addEventListener('click',()=>brightness(true));
  $('restoreLaptop').addEventListener('click',()=>brightness(false));
  async function displayOutput(enabled){
    $('externalOff').disabled=$('externalOn').disabled=true;
    try{const result=await data('display-output',{enabled});toast(result.note);await status();await loadPreview();}
    catch(e){toast(e.message);}
    finally{$('externalOff').disabled=$('externalOn').disabled=false;}
  }
  $('externalOff').addEventListener('click',()=>displayOutput(false));
  $('externalOn').addEventListener('click',()=>displayOutput(true));
  $('pairToken').addEventListener('keydown',e=>{if(e.key==='Enter')pair();});
  $('settingsButton').addEventListener('click',()=>{
    if(state.pending){toast('먼저 이전 전송 결과를 확인하거나 화면 확인 후 새로 작성을 누르세요.');return;}
    if(!confirm('이 폰에 저장된 연결을 해제할까요? 다시 연결하려면 노트북의 QR 코드를 스캔하면 됩니다.'))return;
    forgetToken();state.token='';$('pairToken').value='';
    showPaired(false);
  });
  $('checkPending').addEventListener('click',checkPending);
  $('clearPending').addEventListener('click',()=>{if(confirm('노트북 화면에서 이전 메시지의 전송 여부를 확인했나요? 새 전송은 중복될 수 있습니다.'))savePending(null);});
  $('showSessions').addEventListener('click',()=>showTab('sessions'));
  $('showPreview').addEventListener('click',()=>showTab('preview'));
  $('refreshSessions').addEventListener('click',()=>listSessions());
  $('moreSessions').addEventListener('click',()=>listSessions(true));
  $('sessionSearch').addEventListener('input',renderSessions);
  $('refreshPreview').addEventListener('click',loadPreview);
  $('activateDesktop').addEventListener('click',async()=>{try{await data('activate',{});await loadPreview();await status();}catch(e){toast(e.message);}});
  $('scrollUp').addEventListener('click',()=>remoteScroll(3));
  $('scrollDown').addEventListener('click',()=>remoteScroll(-3));
  $('zoomIn').addEventListener('click',()=>{state.zoom=Math.min(4,Math.round((state.zoom+.5)*10)/10);$('previewImage').style.width=(state.zoom*100)+'%';});
  $('zoomOut').addEventListener('click',()=>{state.zoom=Math.max(1,Math.round((state.zoom-.5)*10)/10);$('previewImage').style.width=(state.zoom*100)+'%';});
  $('newSession').addEventListener('click',async()=>{
    if(!confirm('데스크톱 화면에서 현재 Work Local 모드인지 확인했나요?\nCloud 모드에서 생성한 대화는 동기화될 수 있습니다.'))return;
    try{const r=await data('sessions/new',{confirmed_local:true});toast(r.note||'새 세션 버튼을 클릭했습니다.');await listSessions();}
    catch(e){toast(e.message+' 화면 탭에서 직접 조작해도 됩니다.');}
  });
  $('previewImage').addEventListener('click',async(e)=>{
    if(!state.previewUrl)return;
    const rect=e.target.getBoundingClientRect();
    const x=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width));
    const y=Math.max(0,Math.min(1,(e.clientY-rect.top)/rect.height));
    if(!confirm('노트북 ChatGPT 창에서 이 위치를 클릭할까요?'))return;
    try{await data('click',{x,y});await loadPreview();}
    catch(err){toast(err.message);}
  });
  $('composer').addEventListener('submit',send);
  $('message').addEventListener('keydown',e=>{
    if(e.key==='Enter' && (e.metaKey||e.ctrlKey)){e.preventDefault();$('composer').requestSubmit();}
  });
  setInterval(()=>{
    if(state.token){status();if(state.tab==='preview' && $('autoPreview').checked)loadPreview();}
  },2000);
  showPaired(false);
  if(state.token){showPaired(true);if(state.pending)$('message').value=state.pending.text;status();}
  if(state.pending)savePending(state.pending);
})();
