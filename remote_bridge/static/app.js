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
  let savedChat=null;try{savedChat=JSON.parse(sessionStorage.getItem('remote-chat')||'null');}catch{}
  const state = {selected:null, messages:[], historyCursor:null, selecting:false, waitingUntil:0, token:initialToken, tab:'sessions', sessions:[], nextCursor:null, previewUrl:null, refreshing:false, zoom:1, online:false, serverInstance:null, pending};
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
      if(reconnect){listSessions();if(state.selected)loadMessages();}
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
      if(savedChat && !state.selected && state.tab==='sessions'){const chat=state.sessions.find(s=>s.id===savedChat.id)||savedChat;savedChat=null;openChat(chat,false);}
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
      b.textContent=s.title;b.className=state.selected?.id===s.id?'selected':'';
      const tag=document.createElement('small');
      tag.textContent=(s.project_name||(s.project?'프로젝트 대화':'로컬 대화'))+(s.updated_at?' · '+new Date(s.updated_at*1000).toLocaleString('ko-KR'): '');b.append(tag);
      b.addEventListener('click',async()=>{
        await openChat(s);
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
    $('showChat').classList.toggle('active',tab==='chat');
    $('chatPane').hidden=(tab!=='chat');
    $('sessionsPane').hidden=(tab!=='sessions');
    $('previewPane').hidden=(tab!=='preview');
    if(tab==='preview')loadPreview();
    if(tab==='chat' && state.selected)loadMessages();
  }
  async function send(ev){
    ev.preventDefault();
    const text=$('message').value;
    if(!text.trim() || state.selecting)return;
    if(!state.selected && !state.newComposer){toast('대화 목록에서 메시지를 보낼 채팅을 선택해 주세요.');return;}
    $('sendButton').disabled=true;
    try{
      if(state.pending && state.pending.text!==text){toast('이전 전송 결과를 먼저 확인하거나 화면 확인 후 새로 작성을 누르세요.');return;}
      if(!state.pending){
        if(!await status())throw Error('연결이 복구되면 다시 보내세요.');
        const request_id = (globalThis.crypto?.randomUUID?.() || String(Date.now())+'_'+Math.random().toString(36).slice(2)).replace(/[^a-zA-Z0-9_-]/g,'');
        savePending({text,request_id,server_instance:state.serverInstance,thread_id:state.selected?.id||null});
      }
      const result=await data('send',state.pending);
      if(result.input_sent){savePending(null);$('message').value='';$('message').style.height='auto';state.waitingUntil=Date.now()+45000;toast('전송했어요. 답변을 확인하고 있어요.');if(state.selected){showTab('chat');loadMessages();}else{listSessions();toast('첫 메시지를 전송했어요. 대화 목록에서 새 대화를 선택해 주세요.');}}
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

  async function openChat(chat, activate=true){
    if(state.selecting)return;
    if(state.pending && state.pending.thread_id && state.pending.thread_id!==chat.id){toast('이전 대화의 전송 결과를 먼저 확인해 주세요.');return;}
    state.selecting=true;
    try{
      if(activate)await data('sessions/select',{id:chat.id});
      state.selected={id:chat.id,title:chat.title};state.newComposer=false;state.messages=[];state.historyCursor=null;state.waitingUntil=0;
      sessionStorage.setItem('remote-chat',JSON.stringify(state.selected));
      $('chatTitle').textContent=chat.title;$('message').placeholder='메시지 보내기';
      $('chatState').textContent='대화를 불러오는 중…';$('messageList').replaceChildren();
      $('chatEmpty').hidden=true;$('olderMessages').hidden=true;$('replyState').hidden=true;$('jumpLatest').hidden=true;
      showTab('chat');renderSessions();
    }catch(e){toast(e.message);}
    finally{state.selecting=false;}
  }
  function nearBottom(){const el=$('messageScroll');return el.scrollHeight-el.scrollTop-el.clientHeight<100;}
  function scrollLatest(){const el=$('messageScroll');el.scrollTop=el.scrollHeight;$('jumpLatest').hidden=true;}
  async function copyText(text){try{await navigator.clipboard.writeText(text);toast('복사했어요.');}catch{toast('길게 눌러 텍스트를 선택해 복사해 주세요.');}}
  function messageNode(message){
    const row=document.createElement('article');row.className='message-row '+message.role;row.dataset.messageId=message.id;
    const author=document.createElement('div');author.className='message-author';author.textContent=message.role==='user'?'나':'ChatGPT';
    const body=document.createElement('div');body.className='message-body';
    if(message.role==='assistant' && globalThis.marked && globalThis.DOMPurify){
      // Local Markdown renderer + allowlisted sanitizer. Never load remote message images.
      body.innerHTML=DOMPurify.sanitize(marked.parse(message.text||'',{gfm:true,breaks:true}),{
        ALLOWED_TAGS:['p','br','strong','em','del','s','a','code','pre','blockquote','ul','ol','li','h1','h2','h3','h4','h5','h6','hr','table','thead','tbody','tr','th','td','sup','sub'],
        ALLOWED_ATTR:['href','title','start','colspan','rowspan'],ALLOW_DATA_ATTR:false
      });
      body.querySelectorAll('a').forEach(a=>{const href=a.getAttribute('href')||'';if(!/^https?:\/\//i.test(href)&&!/^mailto:/i.test(href)){a.removeAttribute('href');a.title='노트북에서 열 수 있는 파일 또는 링크';}else{a.target='_blank';a.rel='noopener noreferrer';}});
      body.querySelectorAll('pre').forEach(pre=>{const text=pre.textContent;const button=document.createElement('button');button.type='button';button.className='copy-code';button.textContent='코드 복사';button.addEventListener('click',()=>copyText(text));pre.prepend(button);});
      body.querySelectorAll('table').forEach(table=>{const wrap=document.createElement('div');wrap.className='table-wrap';table.replaceWith(wrap);wrap.append(table);});
    }else body.textContent=message.text||'';
    row.append(author,body);
    for(const name of message.attachments||[]){const el=document.createElement('div');el.className='attachment';el.textContent='▧ '+name+' · 원본은 노트북에서 확인';row.append(el);}
    if(message.role==='assistant'){
      const actions=document.createElement('div');actions.className='message-actions';
      const copy=document.createElement('button');copy.type='button';copy.textContent='답변 복사';copy.addEventListener('click',()=>copyText(message.text));actions.append(copy);row.append(actions);
    }
    row._content=JSON.stringify(message);return row;
  }
  function renderMessages(){
    const list=$('messageList'),existing=new Map(Array.from(list.children).map(el=>[el.dataset.messageId,el]));
    // Retain unchanged DOM nodes, including the reader's text selection and code scroll.
    let at=list.firstElementChild;
    for(const message of state.messages){
      let row=existing.get(message.id);
      if(!row || row._content!==JSON.stringify(message)){const replacement=messageNode(message);if(row){if(row===at)at=replacement;row.replaceWith(replacement);}row=replacement;}
      if(row!==at)list.insertBefore(row,at);
      at=row.nextElementSibling;existing.delete(message.id);
    }
    existing.forEach(el=>el.remove());
  }
  const historyRequests=new Set();
  async function loadMessages(older=false,manual=false){
    const chat=state.selected;if(!chat || document.hidden || (older&&!state.historyCursor))return;
    const key=chat.id+(older?':older':':latest');if(historyRequests.has(key))return;historyRequests.add(key);
    const el=$('messageScroll'), first=state.messages.length===0, stick=first||nearBottom();
    const previousHeight=el.scrollHeight,previousTop=el.scrollTop;
    try{
      const result=await data('chats/'+encodeURIComponent(chat.id)+'/messages'+(older?'?cursor='+encodeURIComponent(state.historyCursor):''));
      if(state.selected!==chat)return;
      const previous=JSON.stringify(state.messages),fresh=result.messages||[];
      // Replace the complete turns in this page so removed/updated items cannot linger.
      const turns=new Set(fresh.map(m=>m.turn_id).filter(Boolean));
      const kept=state.messages.filter(m=>!turns.has(m.turn_id));
      const combined=older?[...fresh,...kept]:[...kept,...fresh];
      state.messages=[...new Map(combined.map(m=>[m.id,m])).values()];
      if(first||older)state.historyCursor=result.next_cursor||null;
      $('olderMessages').hidden=!state.historyCursor;
      const changed=previous!==JSON.stringify(state.messages);
      if(changed)renderMessages();
      $('chatEmpty').hidden=state.messages.length>0;
      if(older)el.scrollTop=previousTop+(el.scrollHeight-previousHeight);
      else if(stick && changed)scrollLatest();
      else if(changed)$('jumpLatest').hidden=false;
      const latest=state.messages[state.messages.length-1];
      if(latest?.role==='assistant' && latest.turn_status==='completed' && changed)state.waitingUntil=0;
      const busy=result.turn_status==='inProgress'||result.turn_status==='pending';
      const waiting=state.waitingUntil>Date.now();
      $('replyState').hidden=!(busy||waiting||result.error);
      $('replyState').classList.toggle('busy',busy||waiting);
      $('replyState').textContent=result.error || (busy?'응답 중 · 저장되는 내용을 확인하고 있어요':waiting?'노트북에 전달했어요 · 새 답변을 기다리는 중':'');
      $('chatState').textContent=(busy?'응답 중 · ':'')+'대화 자동 갱신 · 2초 간격';
      if(manual)toast(older?'이전 메시지를 불러왔어요.':'대화를 새로고침했어요.');
    }catch(e){if(state.selected===chat){$('chatState').textContent=e.message;if(manual)toast(e.message);}}
    finally{historyRequests.delete(key);}
  }
  $('message').addEventListener('input',()=>{const el=$('message');el.style.height='auto';el.style.height=Math.min(el.scrollHeight,150)+'px';});
  if(globalThis.visualViewport){const resize=()=>{document.documentElement.style.setProperty('--app-height',visualViewport.height+'px');};visualViewport.addEventListener('resize',resize);resize();}

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
  $('showChat').addEventListener('click',()=>showTab('chat'));
  $('refreshChat').addEventListener('click',()=>loadMessages(false,true));
  $('olderMessages').addEventListener('click',()=>loadMessages(true,true));
  $('jumpLatest').addEventListener('click',scrollLatest);
  $('messageScroll').addEventListener('scroll',()=>{if(nearBottom())$('jumpLatest').hidden=true;});
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
    try{const r=await data('sessions/new',{confirmed_local:true});state.selected=null;state.newComposer=true;state.messages=[];sessionStorage.removeItem('remote-chat');$('messageList').replaceChildren();$('chatTitle').textContent='새 대화';$('chatState').textContent='첫 메시지를 보낸 뒤 대화 목록에서 선택해 주세요.';$('chatEmpty').hidden=false;$('olderMessages').hidden=true;$('replyState').hidden=true;toast(r.note||'새 대화를 열었어요.');showTab('chat');await listSessions();}
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
    if(state.token && !document.hidden){status();if(state.tab==='preview' && $('autoPreview').checked)loadPreview();if(state.tab==='chat' && state.selected)loadMessages();}
  },2000);
  showPaired(false);
  if(state.token){showPaired(true);if(state.pending)$('message').value=state.pending.text;status();}
  if(state.pending)savePending(state.pending);
})();
