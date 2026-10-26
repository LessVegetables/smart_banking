import {useEffect,useState,type FormEvent} from 'react';
import {Activity,ArrowLeft,Check,Copy,Dices,LockKeyhole,LogOut,RefreshCw,Save,Server} from 'lucide-react';
import {api,money,type User} from './types';
import './organizers.css';

type Outcome='default'|'ok'|'error'|'timeout';
type SourceSetting={outcome:Outcome;latency_ms:number|null};
type Config={revision:number;sources:Record<string,SourceSetting>;updated_at:string|null};
const sources=[
 ['egrul','ЕГРЮЛ / ЕГРИП','Реестр компаний',true,false],
 ['bki','БКИ','Кредитная история',true,false],
 ['aml','115-ФЗ','Проверка операций',true,false],
 ['accounts','Счета банка','Обороты по счетам',true,true],
 ['client_card','Карточка клиента','Профиль и стоп-лист',true,true],
 ['fns','ФНС','Налоги и выручка',false,false],
 ['fssp','ФССП','Исполнительные производства',false,false],
 ['arbitration','Арбитраж / Федресурс','Суды и банкротство',false,false],
] as const;
type RandomCompany={inn:string;ogrn:string;legal_form:'ip'|'ooo';name:string;status:'active'|'liquidating'|'reorganizing'|'liquidated';registered_at:string;okved_main:string;industry_key:string;industry:string;region:string;address:string;director:string;size:'micro'|'small';employees:number|null;revenue:number|null;revenue_year:number|null;demo_persona:string|null};
const industryEmoji:Record<string,string>={bakery:'🥐',retail_food:'🛒',wholesale:'📦',construction:'🏗️',transport:'🚚',restaurants:'🍝',beauty:'💇',it:'💻',agriculture:'🌾',auto_repair:'🔧',manufacturing:'⚙️',tourism:'🏝️'};
const statusLabels:Record<RandomCompany['status'],string>={active:'Действует',liquidating:'В ликвидации',reorganizing:'Реорганизуется',liquidated:'Ликвидирована'};
const labels:Record<Outcome,string>={default:'По умолчанию',ok:'Работает',error:'Ошибка',timeout:'Тайм-аут'};

export default function Organizers(){
 const [user,setUser]=useState<User|null>(null),[ready,setReady]=useState(false),[password,setPassword]=useState('');
 const [config,setConfig]=useState<Config|null>(null),[draft,setDraft]=useState<Config['sources']>({});
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const dirty=!!config&&sources.some(([id])=>config.sources[id].outcome!==draft[id]?.outcome||config.sources[id].latency_ms!==draft[id]?.latency_ms);
 const [company,setCompany]=useState<RandomCompany|null>(null),[rolling,setRolling]=useState(false),[copied,setCopied]=useState(false);
 async function roll(){setRolling(true);setError('');setCopied(false);try{setCompany(await api<RandomCompany>('/organizers/random-company'));}catch(e){setError((e as Error).message);}finally{setRolling(false);}}
 async function copyInn(){if(!company)return;try{await navigator.clipboard.writeText(company.inn);setCopied(true);}catch{const range=document.createRange(),node=document.getElementById('random-inn');if(node){range.selectNodeContents(node);getSelection()?.removeAllRanges();getSelection()?.addRange(range);}}}
 async function load(){const result=await api<Config>('/organizers/sources');setConfig(result);setDraft(result.sources);}
 async function action(fn:()=>Promise<void>){setBusy(true);setError('');setNotice('');try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 useEffect(()=>{let active=true;api<User>('/auth/me').then(value=>{if(active)setUser(value);}).catch(()=>{}).finally(()=>{if(active)setReady(true);});return()=>{active=false;};},[]);
 useEffect(()=>{if(user?.role==='staff')void action(load);},[user?.id]);
 async function login(e:FormEvent){e.preventDefault();await action(async()=>{setUser(await api<User>('/auth/staff','POST',{password}));setPassword('');});}
 async function save(e:FormEvent){e.preventDefault();if(!config)return;await action(async()=>{const result=await api<Config>('/organizers/sources','PUT',{revision:config.revision,sources:draft});setConfig(result);setDraft(result.sources);setNotice('Настройки сохранены. Следующие запросы используют новый режим.');});}
 function change(id:string,patch:Partial<SourceSetting>){setDraft(prev=>({...prev,[id]:{...prev[id],...patch}}));setNotice('');}
 return <main className="organizers-page">
  <header className="organizers-header"><a href="/" className="text-button"><ArrowLeft size={16}/>В кабинет</a><span className="organizers-badge"><Activity size={15}/>Демонстрационный стенд</span>{user?.role==='staff'&&<button className="text-button" disabled={busy} onClick={()=>void action(async()=>{await api('/auth/logout','POST');setUser(null);setConfig(null);})}><LogOut size={16}/>Выйти</button>}</header>
  <div className="organizers-heading"><p className="eyebrow">ДЛЯ ОРГАНИЗАТОРОВ</p><h1>Управление источниками</h1><p>Проверьте, как кредитный конвейер реагирует на задержки и недоступность данных.</p></div>
  {error&&<div className="organizers-message organizers-error" role="alert">{error}</div>}
  {!ready?<p role="status">Загружаем настройки…</p>:user?.role!=='staff'?<form className="card organizers-login" onSubmit={login}><LockKeyhole size={26}/><h2>Вход организатора</h2><p>Используйте пароль сотрудника демонстрационного стенда.</p><label className="field-label">Пароль<input required type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} disabled={busy}/></label><button className="button primary" disabled={busy||!password}>{busy?'Входим…':'Войти'}</button></form>:<>
   <section className="card organizers-random"><div className="organizers-random-intro"><span className="icon-tile"><Dices size={22}/></span><div><h2>Случайный ИНН из синтетического мира</h2><p>Возьмите компанию для проверки конвейера. Скрытые параметры мира — качество, архетип и исход — не показываются.</p></div><button className="button primary" disabled={rolling} onClick={()=>void roll()}><Dices size={17}/>{rolling?'Выбираем…':company?'Другой ИНН':'Получить ИНН'}</button></div>
    {company&&<article className={'organizers-company industry-'+company.industry_key} aria-live="polite"><div className="organizers-company-top"><span className="organizers-company-emoji" aria-hidden="true">{industryEmoji[company.industry_key]??'🏢'}</span><div><small>{company.legal_form==='ip'?'ИП':'ООО'} · {company.industry}</small><h3>{company.name}</h3></div><span className={'organizers-company-status status-'+company.status}>{statusLabels[company.status]}</span></div>
     <div className="organizers-inn"><span>ИНН</span><code id="random-inn">{company.inn}</code><button type="button" className="button secondary" onClick={()=>void copyInn()}>{copied?<Check size={16}/>:<Copy size={16}/>}{copied?'Скопировано':'Скопировать'}</button></div>
     <dl className="organizers-company-facts"><div><dt>Руководитель</dt><dd>{company.director}</dd></div><div><dt>Регион</dt><dd>{company.region}</dd></div><div><dt>На рынке с</dt><dd>{new Date(company.registered_at).getFullYear()} года</dd></div><div><dt>Сотрудники</dt><dd>{company.employees??'—'} · {company.size==='micro'?'микро':'малый'}</dd></div><div><dt>{company.revenue_year?`Выручка за ${company.revenue_year}`:'Выручка'}</dt><dd>{company.revenue==null?'нет отчётности':money(company.revenue)}</dd></div><div><dt>ОКВЭД / ОГРН</dt><dd>{company.okved_main} · {company.ogrn}</dd></div></dl>
     {company.demo_persona&&<p className="organizers-company-persona">Это демо-персона «{company.demo_persona}»</p>}</article>}
   </section>
   <section className="organizers-info"><Server size={24}/><div><b>8 синтетических источников · единые настройки для всего стенда</b><p>Ошибка обязательного источника передаст заявку сотруднику после повторных попыток. При сбое необязательного источника проверка продолжится с отметкой о недоступности. Уже принятые решения не меняются.</p></div></section>
   <div className="organizers-toolbar"><span>{config?`Версия настроек ${config.revision}`:'Загрузка…'}{dirty?' · Есть несохранённые изменения':''}</span><button className="button secondary" disabled={busy} onClick={()=>void action(load)}><RefreshCw size={16}/>Обновить настройки</button></div>
   {config&&<form onSubmit={save}><fieldset disabled={busy} className="organizers-fields"><div className="organizers-grid">{sources.map(([id,name,description,required,internal])=>{const value=draft[id];return <article className="card organizers-source" key={id}><div className="organizers-source-heading"><span className="icon-tile"><Server size={20}/></span><span className={'organizers-state state-'+value.outcome}>{labels[value.outcome]}</span></div><h2>{name}</h2><p>{description}</p><div className="organizers-tags"><span>{required?'Обязательный':'Необязательный'}</span><span>{internal?'Внутренний':'Внешний'}</span></div><label className="field-label">Режим ответа<select value={value.outcome} onChange={e=>change(id,{outcome:e.target.value as Outcome})}>{Object.entries(labels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><label className="field-label">Задержка, мс<input type="number" min="0" max="30000" step="1" placeholder="По умолчанию" disabled={value.outcome==='timeout'} value={value.latency_ms??''} onChange={e=>change(id,{latency_ms:e.target.value===''?null:Number(e.target.value)})}/></label><small>{value.outcome==='timeout'?'Источник зависает до тайм-аута; задержка не применяется.':internal?'Принудительный сбой позволяет проверить отказ внутреннего источника.':'Пустая задержка использует текущие параметры стенда.'}</small></article>;})}</div>
   <div className="organizers-actions"><div><b>{dirty?'Изменения ещё не применены':'Настройки актуальны'}</b><p>«По умолчанию» сохраняет сценарии из конфигурации стенда, включая сбои отдельных демо-компаний.</p></div><button type="button" className="button secondary" onClick={()=>{setDraft(Object.fromEntries(sources.map(([id])=>[id,{outcome:'default',latency_ms:null}])));setNotice('Для применения сброса нажмите «Сохранить».');}}>Сбросить к исходным</button><button className="button primary" disabled={!dirty}><Save size={17}/>{busy?'Сохраняем…':'Сохранить'}</button></div></fieldset></form>}
   {notice&&<div className="organizers-message" role="status"><Check size={18}/>{notice}</div>}
  </>}
 </main>;
}
