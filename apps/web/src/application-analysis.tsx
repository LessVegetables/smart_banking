import {useEffect,useMemo,useState} from 'react';
import {ArrowRight,CheckCircle2,Clock,LoaderCircle,RefreshCw,ShieldCheck} from 'lucide-react';
import {api,money,type V2Application,type V2SourceProgress} from './types';
import './application-analysis.css';

const sourceNames:Record<string,string>={accounts:'Счета Альфа-Банка',client_card:'Карточка клиента',egrul:'ЕГРЮЛ / ЕГРИП',bki:'Кредитная история',fns:'ФНС',fssp:'ФССП',arbitration:'Арбитраж и Федресурс',aml:'Проверка 115-ФЗ'};
const sourceStates:Record<string,string>={started:'Запрашиваем',ok:'Получен ответ',timeout:'Повторяем запрос',error:'Повторяем запрос',not_found:'Нет данных',no_consent:'Пропущен без согласия'};
const inProgress=new Set(['submitted','enriching','scoring']);
const steps=[{id:'enriching',label:'Собираем данные бизнеса'},{id:'scoring',label:'Рассчитываем условия'}];

function latestBySource(sources:V2SourceProgress[]){
 const latest=new Map<string,V2SourceProgress>();
 for(const source of sources){const old=latest.get(source.source);if(!old||source.at>=old.at||source.attempt>=old.attempt)latest.set(source.source,source);}
 return [...latest.values()];
}

function decisionHeading(app:V2Application){
 if(app.status==='offered')return 'Подобрали варианты финансирования';
 if(app.status==='needs_data')return 'Нужно дополнить данные';
 if(app.status==='in_review')return 'Заявка передана специалисту';
 if(app.status==='declined')return 'По заявке нет предложения';
 return app.status_text;
}

export default function ApplicationAnalysis({applicationId,onContinue}:{applicationId:string;onContinue:()=>void}){
 const [app,setApp]=useState<V2Application|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);
 const sources=useMemo(()=>latestBySource(app?.sources??[]),[app?.sources]);
 useEffect(()=>{
  let active=true;
  const load=async()=>{try{const next=await api<V2Application>(`/v2/applications/${applicationId}`);if(active){setApp(next);setError('');}}catch(e){if(active)setError((e as Error).message);}finally{if(active)setLoading(false);}};
  void load();
  const timer=window.setInterval(()=>{if(!app||inProgress.has(app.status))void load();},1200);
  return()=>{active=false;window.clearInterval(timer);};
 },[applicationId,app?.status]);
 const running=!app||inProgress.has(app.status);
 const currentStep=app?.status==='scoring'?1:0;
 const decision=app?.decision;
 return <div className="content v2-analysis">
  <p className="eyebrow">ЗАЯВКА № {applicationId.slice(0,8).toUpperCase()}</p>
  {running?<>
   <div className="analysis-title"><span className="analysis-pulse"><LoaderCircle className="spin" size={23}/></span><div><h1>Анализируем ваш бизнес<span className="hello-dot">.</span></h1><p className="lead">Собираем данные из источников и рассчитываем условия. Обычно это занимает меньше минуты.</p></div></div>
   <section className="card analysis-steps" aria-label="Этапы обработки">
    {steps.map((step,index)=><div className={'analysis-step '+(index<currentStep?'complete':index===currentStep?'current':'')} key={step.id}><span>{index<currentStep?<CheckCircle2 size={19}/>:index===currentStep?<LoaderCircle size={18} className="spin"/>:<i/>}</span><b>{step.label}</b></div>)}
   </section>
   <section className="card analysis-sources"><div className="analysis-section-title"><div><h2>Проверки источников</h2><p>Статус обновляется по мере поступления ответов</p></div><Clock size={18}/></div>
    {sources.length?<div className="source-list">{sources.map((source,index)=><SourceRow key={`${source.source}-${index}`} source={source}/>)}</div>:<p className="source-wait">Подключаем источники…</p>}
   </section>
   <p className="analysis-privacy"><ShieldCheck size={15}/> Ваши данные защищены и используются для рассмотрения заявки.</p>
  </>:<>
   <section className="card analysis-result"><span className="icon-tile red"><CheckCircle2 size={24}/></span><p className="eyebrow">АНАЛИЗ ЗАВЕРШЁН</p><h1>{app?decisionHeading(app):'Результат готов'}<span className="hello-dot">.</span></h1><p className="lead">{app?.status_text}</p>
    {decision?.data_quality?.client_message&&<p className="analysis-result-message">{decision.data_quality.client_message}</p>}
    {decision?.reasons?.map((reason,index)=><p className="analysis-result-message" key={index}>{reason}</p>)}
    {decision?.route_messages?.map((message,index)=><p className="analysis-result-message" key={`route-${index}`}>{message}</p>)}
    {decision?.requested_documents?.length? <div className="analysis-documents"><b>Что понадобится</b>{decision.requested_documents.map((document,index)=><span key={index}>{document}</span>)}</div>:null}
    {decision?.offers?.length?<div className="analysis-offers"><b>{decision.offers.length===1?'Доступный вариант':'Доступные варианты'}</b>{decision.offers.map((offer,index)=><div className="analysis-offer" key={offer.offer_id}><strong>{money(offer.amount)}</strong><span>{offer.term_months} мес. · {Math.round(offer.rate*1000)/10}% годовых · платёж {money(offer.monthly_payment)} / мес.</span></div>)}</div>:null}
    {app?.status==='in_review'&&<p className="analysis-result-message">Специалист проверит заявку и сообщит о следующем шаге.</p>}
    <button className="button primary" onClick={onContinue}>Продолжить <ArrowRight size={17}/></button>
   </section>
  </>}
  {error&&<div className="card analysis-error" role="alert"><p>{error}</p><button className="button secondary" onClick={()=>{setLoading(true);setError('');void api<V2Application>(`/v2/applications/${applicationId}`).then(setApp).catch(e=>setError((e as Error).message)).finally(()=>setLoading(false));}}><RefreshCw size={15}/>Повторить</button></div>}
  <button className="analysis-back" onClick={onContinue}>Вернуться в кабинет</button>
 </div>;
}

function SourceRow({source}:{source:V2SourceProgress}){
 const settled=source.final;
 const outcome=source.final&&['timeout','error'].includes(source.status)?'Источник временно недоступен':sourceStates[source.status]??'Обрабатываем';
 return <div className="source-row"><span className={'source-indicator '+(settled?'settled':'')}>{settled?<CheckCircle2 size={16}/>:<LoaderCircle size={16} className="spin"/>}</span><div className="source-copy"><b>{sourceNames[source.source]??source.source}</b><small>{outcome}</small></div><span className="source-attempt">{settled?'Завершено':source.status==='started'?`Попытка ${source.attempt}`:`Повтор ${source.attempt}`}</span></div>;
}
