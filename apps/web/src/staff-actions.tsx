import {useEffect,useMemo,useState} from 'react';
import {Check,RefreshCw,ShieldCheck} from 'lucide-react';
import {api,money,productNames,type User,type V2StaffApplication,type V2StaffOverrides,type V2StaffReasonCodes} from './types';
import './staff-actions.css';

type Action='approve'|'change'|'decline'|'request_data'|'rerun';
const actionNames:Record<Action,string>={approve:'Одобрить предложения',change:'Изменить предложение',decline:'Отказать',request_data:'Запросить данные',rerun:'Перезапустить расчёт'};
const positionNames={underwriter:'Андеррайтер',risk_manager:'Риск-менеджер'} as const;

export default function StaffActions({app,user,onUpdated}:{app:V2StaffApplication;user:User;onUpdated:()=>Promise<void>}){
 const [codes,setCodes]=useState<V2StaffReasonCodes|null>(null),[stats,setStats]=useState<V2StaffOverrides|null>(null);
 const [action,setAction]=useState<Action>('approve'),[reason,setReason]=useState(''),[comment,setComment]=useState(''),[selectedOffers,setSelectedOffers]=useState<string[]>([]);
 const [amount,setAmount]=useState(''),[term,setTerm]=useState('12'),[product,setProduct]=useState(''),[documents,setDocuments]=useState('');
 const [signComment,setSignComment]=useState(''),[signReason,setSignReason]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 useEffect(()=>{let active=true;Promise.all([api<V2StaffReasonCodes>('/v2/staff/reason-codes'),api<V2StaffOverrides>('/v2/staff/overrides')]).then(([c,s])=>{if(active){setCodes(c);setStats(s);}}).catch(e=>{if(active)setError((e as Error).message);});return()=>{active=false;};},[user.id]);
 const choices=action==='approve'||action==='change'||action==='decline'||action==='request_data'||action==='rerun'?codes?.actions[action]||{}:{};
 const reasonEntries=useMemo(()=>Object.entries(choices),[choices]);
 useEffect(()=>{if(reasonEntries.length&&!reasonEntries.some(([code])=>code===reason))setReason(reasonEntries[0][0]);},[reasonEntries,reason]);
 const pending=app.pending_review;
 const signatures=Array.isArray(pending?.signatures)?pending.signatures as {user_id:string;position:string;at:string}[]:[];
 const neededPosition=signatures[0]?.position==='underwriter'?'risk_manager':'underwriter';
 const sameUser=signatures.some(signature=>signature.user_id===user.id);
 const otherPosition=signatures.some(signature=>signature.position===user.staff_position);
 const canSign=!sameUser&&!otherPosition&&user.staff_position===neededPosition;
 const signatureRejectCodes=Object.entries(codes?.reject||{});
 useEffect(()=>{if(signatureRejectCodes.length&&!signatureRejectCodes.some(([code])=>code===signReason))setSignReason(signatureRejectCodes[0][0]);},[codes,signReason]);
 const offers=app.decision?.offers||[];
 function toggleOffer(id:string){setSelectedOffers(current=>current.includes(id)?current.filter(value=>value!==id):[...current,id]);}
 async function submit(e:React.FormEvent){e.preventDefault();setBusy(true);setError('');setNotice('');try{
  const body:Record<string,unknown>={action,reason_code:reason,comment,revision:app.revision};
  if(action==='approve')body.offer_ids=selectedOffers;
  if(action==='change')body.changes=[{amount:Number(amount),term_months:Number(term),...(product?{product}:{})}];
  if(action==='request_data')body.documents=documents.split(/[\n,;]/).map(value=>value.trim()).filter(Boolean);
  const result=await api<{outcome:string}>('/v2/applications/'+encodeURIComponent(app.id)+'/review','POST',body);
  setNotice(action==='rerun'?'Автоматический расчёт перезапущен.':result.outcome==='proposed'?'Решение предложено комитету. Заявка ждёт вторую подпись.':'Действие применено.');setComment('');setDocuments('');await onUpdated();
  const fresh=await api<V2StaffOverrides>('/v2/staff/overrides');setStats(fresh);
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function sign(approve:boolean){setBusy(true);setError('');setNotice('');try{
  const body={approve,comment:signComment,revision:app.revision,...(!approve?{reason_code:signReason}:{})};
  const result=await api<{outcome:string}>('/v2/applications/'+encodeURIComponent(app.id)+'/review/sign','POST',body);
  setNotice(result.outcome==='applied'?'Вторая подпись принята. Решение применено.':'Предложение комитета отклонено.');setSignComment('');await onUpdated();
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const commentReady=comment.trim().length>=10,signReady=signComment.trim().length>=10;
 return <section className="card staff-dossier-section staff-actions-card">
  <div className="staff-section-title"><h3>Действия по заявке</h3><p>Каждое решение требует кода причины и комментария от 10 символов.</p></div>
  {stats&&<div className="staff-override-summary"><span>Применённых решений: <b>{stats.decisions}</b></span><span>Несогласий с системой: <b>{stats.overrides}</b></span><span>Доля override: <b>{stats.override_rate===null?'—':`${(stats.override_rate*100).toLocaleString('ru-RU',{maximumFractionDigits:1})}%`}</b></span></div>}
  {pending?<div className="staff-pending-review"><div className="staff-pending-title"><ShieldCheck size={18}/><div><b>Решение ожидает второй подписи</b><small>Для комитета нужны подписи андеррайтера и риск-менеджера от разных сотрудников.</small></div></div>
   <div className="staff-signatures">{signatures.map((signature,index)=><div key={`${signature.user_id}-${index}`}><Check size={15}/><span>{positionNames[signature.position as keyof typeof positionNames]||signature.position}</span><small>{signature.user_id===user.id?'Вы':`Сотрудник ${signature.user_id.slice(0,8)}`}</small></div>)}</div>
   {canSign?<><label className="field-label">Комментарий к подписи · от 10 символов<textarea value={signComment} onChange={e=>setSignComment(e.target.value)} placeholder="Почему поддерживаете или отклоняете предложение?"/></label><div className="staff-action-buttons"><button className="button primary" disabled={busy||!signReady} onClick={()=>sign(true)}>{busy?'Отправляем…':'Подписать решение'}</button><div className="staff-reject-sign"><label className="field-label">Причина отклонения<select value={signReason} onChange={e=>setSignReason(e.target.value)}>{signatureRejectCodes.map(([code,label])=><option key={code} value={code}>{label}</option>)}</select></label><button className="button secondary" disabled={busy||!signReady||!signReason} onClick={()=>sign(false)}>Отклонить предложение</button></div></div></>:<p className="staff-action-hint">{sameUser?'Войдите во втором профиле сотрудника для второй подписи.':otherPosition?`Для второй подписи нужен ${positionNames[neededPosition]}.`:`Текущая должность не может поставить вторую подпись.`} В другом профиле выберите противоположную должность при входе.</p>}</div>
  :<form onSubmit={submit} className="staff-action-form">
   <label className="field-label">Действие<select value={action} onChange={e=>{setAction(e.target.value as Action);setReason('');}}>{(Object.keys(actionNames) as Action[]).map(value=><option key={value} value={value}>{actionNames[value]}</option>)}</select></label>
   {action==='approve'&&<fieldset className="staff-action-offers"><legend>Предложения для одобрения</legend>{offers.length?offers.map(offer=><label key={offer.offer_id}><input type="checkbox" checked={selectedOffers.includes(offer.offer_id)} onChange={()=>toggleOffer(offer.offer_id)}/><span>{productNames[offer.product]||offer.product} · {money(offer.amount)} · {offer.term_months} мес. · {(offer.rate*100).toLocaleString('ru-RU',{maximumFractionDigits:2})}%</span></label>):<p className="muted">Нет предложений. Выберите отказ, запрос данных или перезапуск расчёта.</p>}</fieldset>}
   {action==='change'&&<div className="staff-change-fields"><label className="field-label">Сумма, ₽<input type="number" min="1" max={app.decision?.offers.reduce((max,offer)=>Math.max(max,offer.limit.limit),0)||undefined} value={amount} onChange={e=>setAmount(e.target.value)} required/></label><label className="field-label">Срок, мес.<input type="number" min="1" max="120" value={term} onChange={e=>setTerm(e.target.value)} required/></label><label className="field-label">Продукт<select value={product} onChange={e=>setProduct(e.target.value)}><option value="">По цели заявки</option>{Object.entries(productNames).map(([value,label])=><option value={value} key={value}>{label}</option>)}</select></label></div>}
   {action==='request_data'&&<label className="field-label">Документы, которые нужно запросить<textarea value={documents} onChange={e=>setDocuments(e.target.value)} placeholder="Например: выписка по счёту за 12 месяцев"/></label>}
   <label className="field-label">Код причины<select value={reason} onChange={e=>setReason(e.target.value)}>{reasonEntries.map(([code,label])=><option key={code} value={code}>{label}</option>)}</select></label>
   <label className="field-label">Комментарий · от 10 символов<textarea value={comment} onChange={e=>setComment(e.target.value)} placeholder="Обоснование действия для журнала заявки"/></label>
   <button className="button primary" disabled={busy||!commentReady||!reason||(action==='approve'&&(!offers.length||!selectedOffers.length))||(action==='change'&&(!Number(amount)||!Number(term)))}>{busy?<><RefreshCw size={15} className="staff-spin"/>Применяем…</>:'Выполнить действие'}</button>
  </form>}
  {notice&&<p className="staff-action-notice" role="status">{notice}</p>}{error&&<p className="staff-error" role="alert">{error}</p>}
 </section>;
}
