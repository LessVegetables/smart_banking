import {useEffect,useMemo,useState} from 'react';
import {ArrowRight,CheckCircle2,Clock,FileText,LoaderCircle,RefreshCw,ShieldCheck,Upload} from 'lucide-react';
import {api,money,type V2Application,type V2Offer,type V2SourceProgress} from './types';
import './application-analysis.css';

const sourceNames:Record<string,string>={accounts:'Счета Альфа-Банка',client_card:'Карточка клиента',egrul:'ЕГРЮЛ / ЕГРИП',bki:'Кредитная история',fns:'ФНС',fssp:'ФССП',arbitration:'Арбитраж и Федресурс',aml:'Проверка 115-ФЗ'};
const sourceStates:Record<string,string>={started:'Запрашиваем',ok:'Получен ответ',timeout:'Повторяем запрос',error:'Повторяем запрос',not_found:'Нет данных',no_consent:'Пропущен без согласия'};
const inProgress=new Set(['submitted','enriching','scoring']);
const steps=[{id:'enriching',label:'Собираем данные бизнеса'},{id:'scoring',label:'Рассчитываем условия'}];
const productNames:Record<string,string>={overdraft:'Овердрафт',working_capital:'Кредит на оборотные средства',investment:'Инвестиционный кредит',credit_line:'Кредитная линия'};
const limitNames:Record<string,string>={cash_flow:'Платёжеспособность бизнеса',leverage:'Подтверждённая выручка',collateral:'Обеспечение',turnover:'Оборот по счёту',product_max:'Максимум по продукту',requested:'Запрошенная сумма'};
const collateralNames:Record<string,string>={owner_guarantee:'Поручительство собственника',equipment_pledge:'Залог оборудования',msp_guarantee:'Гарантия Корпорации МСП'};

function latestBySource(sources:V2SourceProgress[]){const latest=new Map<string,V2SourceProgress>();for(const source of sources){const old=latest.get(source.source);if(!old||source.at>=old.at||source.attempt>=old.attempt)latest.set(source.source,source);}return [...latest.values()];}
function decisionHeading(app:V2Application){if(app.status==='offered')return 'Подобрали варианты финансирования';if(app.status==='needs_data')return 'Нужно дополнить данные';if(app.status==='in_review')return 'Заявка передана специалисту';if(app.status==='declined')return 'По заявке нет предложения';if(['accepted','documents'].includes(app.status))return 'Предложение принято';return app.status_text;}
function date(value:string){return new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(`${value}T00:00:00Z`));}

export default function ApplicationAnalysis({applicationId,onContinue}:{applicationId:string;onContinue:()=>void}){
 const [app,setApp]=useState<V2Application|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false);
 const sources=useMemo(()=>latestBySource(app?.sources??[]),[app?.sources]);
 useEffect(()=>{let active=true;const load=async()=>{try{const next=await api<V2Application>(`/v2/applications/${applicationId}`);if(active){setApp(next);setError('');}}catch(e){if(active)setError((e as Error).message);}finally{if(active)setLoading(false);}};void load();const timer=window.setInterval(()=>{if(!app||inProgress.has(app.status))void load();},1200);return()=>{active=false;window.clearInterval(timer);};},[applicationId,app?.status]);
 const running=!app||inProgress.has(app.status);const currentStep=app?.status==='scoring'?1:0;const decision=app?.decision;
 async function accept(offerId:string){if(!app)return;setBusy(true);setError('');try{const next=await api<V2Application>(`/v2/applications/${app.id}/accept`,'POST',{offer_id:offerId,revision:app.revision});setApp(next);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function upload(files:FileList|null){if(!app||!files?.length)return;const body=new FormData();Array.from(files).forEach(file=>body.append('files',file));body.append('revision',String(app.revision));setBusy(true);setError('');try{setApp(await api<V2Application>(`/v2/applications/${app.id}/statements`,'POST',body));}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function skipDocuments(){if(!app)return;setBusy(true);setError('');try{setApp(await api<V2Application>(`/v2/applications/${app.id}/statements/skip`,'POST',{revision:app.revision}));}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const qualityMessage=decision?.data_quality?.client_message;
 const messages=[...new Set([...(decision?.reasons??[]),...(decision?.route_messages??[])])].filter(message=>message!==qualityMessage);
 return <div className="content v2-analysis">
  <p className="eyebrow">ЗАЯВКА № {applicationId.slice(0,8).toUpperCase()}</p>
  {running?<>
   <div className="analysis-title"><span className="analysis-pulse"><LoaderCircle className="spin" size={23}/></span><div><h1>Анализируем ваш бизнес<span className="hello-dot">.</span></h1><p className="lead">Собираем данные из источников и рассчитываем условия. Обычно это занимает меньше минуты.</p></div></div>
   <section className="card analysis-steps" aria-label="Этапы обработки">{steps.map((step,index)=><div className={'analysis-step '+(index<currentStep?'complete':index===currentStep?'current':'')} key={step.id}><span>{index<currentStep?<CheckCircle2 size={19}/>:index===currentStep?<LoaderCircle size={18} className="spin"/>:<i/>}</span><b>{step.label}</b></div>)}</section>
   <section className="card analysis-sources"><div className="analysis-section-title"><div><h2>Проверки источников</h2><p>Статус обновляется по мере поступления ответов</p></div><Clock size={18}/></div>{sources.length?<div className="source-list">{sources.map((source,index)=><SourceRow key={`${source.source}-${index}`} source={source}/>)}</div>:<p className="source-wait">Подключаем источники…</p>}</section>
   <p className="analysis-privacy"><ShieldCheck size={15}/> Ваши данные защищены и используются для рассмотрения заявки.</p>
  </>:<>
   <section className="card analysis-result"><span className="icon-tile red"><CheckCircle2 size={24}/></span><p className="eyebrow">РЕЗУЛЬТАТ РАССМОТРЕНИЯ</p><h1>{app?decisionHeading(app):'Результат готов'}<span className="hello-dot">.</span></h1><p className="lead">{app?.status_text}</p>
    {messages.map((message,index)=><p className="analysis-result-message" key={index}>{message}</p>)}
    {decision?.data_quality?.client_message&&<div className="quality-hint"><b>Как увеличить доступную сумму</b><p>{decision.data_quality.client_message}</p></div>}
    {decision?.counterfactuals?.length? <div className="analysis-insights"><b>Что может изменить условия</b>{decision.counterfactuals.map((item,index)=><p key={index}>{item.client_text}</p>)}</div>:null}
    {app?.status==='offered'&&decision?.offers?.length?<div className="analysis-offers"><b>{decision.offers.length===1?'Предложение банка':'Выберите подходящий вариант'}</b><div className="analysis-offer-grid">{decision.offers.map(offer=><OfferCard key={offer.offer_id} offer={offer} busy={busy} onAccept={()=>void accept(offer.offer_id)}/>)}</div></div>:null}
    {decision?.requested_documents?.length?<div className="analysis-documents"><b>Для продолжения нужны документы</b><p className="muted small">Загрузите выписки по счетам из других банков в формате JSON. Можно продолжить без них.</p>{decision.requested_documents.map((document,index)=><span key={index}><FileText size={15}/>{document}</span>)}<label className={'analysis-upload '+(busy?'disabled':'')}><Upload size={18}/><b>{busy?'Загружаем…':'Загрузить выписку'}</b><input type="file" accept=".json,application/json" multiple disabled={busy} onChange={event=>{void upload(event.target.files);event.target.value='';}}/></label><button className="text-button" disabled={busy} onClick={()=>void skipDocuments()}>Продолжить без выписки <ArrowRight size={15}/></button></div>:null}
    {app?.status==='in_review'&&<p className="analysis-result-message">Специалист проверит заявку и сообщит о следующем шаге.</p>}
    {app?.status==='declined'&&decision?.reapply_after&&<div className="analysis-reapply"><b>Когда можно обратиться снова</b><p>Повторно подать заявку можно с {date(decision.reapply_after)}.</p></div>}
    {['accepted','documents'].includes(app?.status??'')&&<div className="analysis-insights"><b>Следующий шаг — оформление</b><p>Банк подготовит документы для подписания. В этом демо подписание и выдача показаны отдельно.</p></div>}
    <button className="button primary" onClick={onContinue}>{app?.status==='offered'?'Вернуться в кабинет':'Продолжить'} <ArrowRight size={17}/></button>
   </section>
  </>}
  {error&&<div className="card analysis-error" role="alert"><p>{error}</p><button className="button secondary" onClick={()=>{setLoading(true);setError('');void api<V2Application>(`/v2/applications/${applicationId}`).then(setApp).catch(e=>setError((e as Error).message)).finally(()=>setLoading(false));}}><RefreshCw size={15}/>Повторить</button></div>}
  <button className="analysis-back" onClick={onContinue}>Вернуться в кабинет</button>
 </div>;
}

function OfferCard({offer,busy,onAccept}:{offer:V2Offer;busy:boolean;onAccept:()=>void}){
 const binding=offer.limit.constraints.find(item=>item.name===offer.limit.binding);
 return <article className={'analysis-offer-card '+(offer.matches_request?'requested':'')}>
  <div className="analysis-offer-top"><span>{offer.matches_request?'По вашему запросу':'Альтернативный вариант'}</span><small>Действует до {date(offer.valid_until)}</small></div><h3>{productNames[offer.product]??'Кредитное предложение'}</h3><strong className="analysis-offer-amount">{money(offer.amount)}</strong>
  <div className="analysis-offer-terms"><span><small>Срок</small><b>{offer.term_months} мес.</b></span><span><small>Ставка</small><b>{(offer.rate*100).toLocaleString('ru-RU',{maximumFractionDigits:2})}%</b></span><span><small>Платёж в месяц</small><b>{money(offer.monthly_payment)}</b></span></div>
  <p className="analysis-tradeoff">{offer.tradeoff}</p>
  {binding&&<div className="analysis-binding"><small>Определяющее ограничение суммы</small><b>{limitNames[binding.name]??'Фактор расчёта'} · {money(binding.value??offer.limit.limit)}</b></div>}
  {offer.collateral.length>0&&<p className="analysis-collateral">Обеспечение: {offer.collateral.map(item=>collateralNames[item]??item).join(', ')}</p>}
  {offer.guarantee_fee>0&&<p className="analysis-collateral">Комиссия за гарантию: {(offer.guarantee_fee*100).toLocaleString('ru-RU',{maximumFractionDigits:2})}% годовых</p>}
  <button className="button primary" disabled={busy} onClick={onAccept}>{busy?'Сохраняем…':'Выбрать предложение'} <ArrowRight size={16}/></button>
 </article>;
}
function SourceRow({source}:{source:V2SourceProgress}){const settled=source.final;const outcome=source.final&&['timeout','error'].includes(source.status)?'Источник временно недоступен':sourceStates[source.status]??'Обрабатываем';return <div className="source-row"><span className={'source-indicator '+(settled?'settled':'')}>{settled?<CheckCircle2 size={16}/>:<LoaderCircle size={16} className="spin"/>}</span><div className="source-copy"><b>{sourceNames[source.source]??source.source}</b><small>{outcome}</small></div><span className="source-attempt">{settled?'Завершено':source.status==='started'?`Попытка ${source.attempt}`:`Повтор ${source.attempt}`}</span></div>;}
