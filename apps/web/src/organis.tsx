import {useEffect,useState,type FormEvent} from 'react';
import {Activity,ArrowLeft,Check,LockKeyhole,LogOut,RefreshCw,Save,Server} from 'lucide-react';
import {api,type User} from './types';
import './organis.css';

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
const labels:Record<Outcome,string>={default:'По умолчанию',ok:'Работает',error:'Ошибка',timeout:'Тайм-аут'};

export default function Organis(){
 const [user,setUser]=useState<User|null>(null),[ready,setReady]=useState(false),[password,setPassword]=useState('');
 const [config,setConfig]=useState<Config|null>(null),[draft,setDraft]=useState<Config['sources']>({});
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const dirty=!!config&&sources.some(([id])=>config.sources[id].outcome!==draft[id]?.outcome||config.sources[id].latency_ms!==draft[id]?.latency_ms);
 async function load(){const result=await api<Config>('/organis/sources');setConfig(result);setDraft(result.sources);}
 async function action(fn:()=>Promise<void>){setBusy(true);setError('');setNotice('');try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 useEffect(()=>{let active=true;api<User>('/auth/me').then(value=>{if(active)setUser(value);}).catch(()=>{}).finally(()=>{if(active)setReady(true);});return()=>{active=false;};},[]);
 useEffect(()=>{if(user?.role==='staff')void action(load);},[user?.id]);
 async function login(e:FormEvent){e.preventDefault();await action(async()=>{setUser(await api<User>('/auth/staff','POST',{password}));setPassword('');});}
 async function save(e:FormEvent){e.preventDefault();if(!config)return;await action(async()=>{const result=await api<Config>('/organis/sources','PUT',{revision:config.revision,sources:draft});setConfig(result);setDraft(result.sources);setNotice('Настройки сохранены. Следующие запросы используют новый режим.');});}
 function change(id:string,patch:Partial<SourceSetting>){setDraft(prev=>({...prev,[id]:{...prev[id],...patch}}));setNotice('');}
 return <main className="organis-page">
  <header className="organis-header"><a href="/" className="text-button"><ArrowLeft size={16}/>В кабинет</a><span className="organis-badge"><Activity size={15}/>Демонстрационный стенд</span>{user?.role==='staff'&&<button className="text-button" disabled={busy} onClick={()=>void action(async()=>{await api('/auth/logout','POST');setUser(null);setConfig(null);})}><LogOut size={16}/>Выйти</button>}</header>
  <div className="organis-heading"><p className="eyebrow">ДЛЯ ОРГАНИЗАТОРОВ</p><h1>Управление источниками</h1><p>Проверьте, как кредитный конвейер реагирует на задержки и недоступность данных.</p></div>
  {error&&<div className="organis-message organis-error" role="alert">{error}</div>}
  {!ready?<p role="status">Загружаем настройки…</p>:user?.role!=='staff'?<form className="card organis-login" onSubmit={login}><LockKeyhole size={26}/><h2>Вход организатора</h2><p>Используйте пароль сотрудника демонстрационного стенда.</p><label className="field-label">Пароль<input required type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} disabled={busy}/></label><button className="button primary" disabled={busy||!password}>{busy?'Входим…':'Войти'}</button></form>:<>
   <section className="organis-info"><Server size={24}/><div><b>8 синтетических источников · единые настройки для всего стенда</b><p>Ошибка обязательного источника передаст заявку сотруднику после повторных попыток. При сбое необязательного источника проверка продолжится с отметкой о недоступности. Уже принятые решения не меняются.</p></div></section>
   <div className="organis-toolbar"><span>{config?`Версия настроек ${config.revision}`:'Загрузка…'}{dirty?' · Есть несохранённые изменения':''}</span><button className="button secondary" disabled={busy} onClick={()=>void action(load)}><RefreshCw size={16}/>Обновить настройки</button></div>
   {config&&<form onSubmit={save}><fieldset disabled={busy} className="organis-fields"><div className="organis-grid">{sources.map(([id,name,description,required,internal])=>{const value=draft[id];return <article className="card organis-source" key={id}><div className="organis-source-heading"><span className="icon-tile"><Server size={20}/></span><span className={'organis-state state-'+value.outcome}>{labels[value.outcome]}</span></div><h2>{name}</h2><p>{description}</p><div className="organis-tags"><span>{required?'Обязательный':'Необязательный'}</span><span>{internal?'Внутренний':'Внешний'}</span></div><label className="field-label">Режим ответа<select value={value.outcome} onChange={e=>change(id,{outcome:e.target.value as Outcome})}>{Object.entries(labels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><label className="field-label">Задержка, мс<input type="number" min="0" max="30000" step="1" placeholder="По умолчанию" disabled={value.outcome==='timeout'} value={value.latency_ms??''} onChange={e=>change(id,{latency_ms:e.target.value===''?null:Number(e.target.value)})}/></label><small>{value.outcome==='timeout'?'Источник зависает до тайм-аута; задержка не применяется.':internal?'Принудительный сбой позволяет проверить отказ внутреннего источника.':'Пустая задержка использует текущие параметры стенда.'}</small></article>;})}</div>
   <div className="organis-actions"><div><b>{dirty?'Изменения ещё не применены':'Настройки актуальны'}</b><p>«По умолчанию» сохраняет сценарии из конфигурации стенда, включая сбои отдельных демо-компаний.</p></div><button type="button" className="button secondary" onClick={()=>{setDraft(Object.fromEntries(sources.map(([id])=>[id,{outcome:'default',latency_ms:null}])));setNotice('Для применения сброса нажмите «Сохранить».');}}>Сбросить к исходным</button><button className="button primary" disabled={!dirty}><Save size={17}/>{busy?'Сохраняем…':'Сохранить'}</button></div></fieldset></form>}
   {notice&&<div className="organis-message" role="status"><Check size={18}/>{notice}</div>}
  </>}
 </main>;
}
