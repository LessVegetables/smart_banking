import {useEffect,useState,type FormEvent} from 'react';
import {ArrowLeft,ArrowRight,Building2,Check,LoaderCircle,Search,ShieldCheck} from 'lucide-react';
import {api,type CompanyCard,type Consent,type DemoCompanyOption} from './types';
import './company-onboarding.css';

export type CompanyStepProgress={inn:string;company:CompanyCard|null;confirmed:boolean;bkiCompany:boolean;bkiOwner:boolean;personalData:boolean};
type Props={busy:boolean;initialCompany?:CompanyCard|null;initialConsents?:Consent[];initialProgress?:CompanyStepProgress;onProgressChange:(progress:CompanyStepProgress)=>void;onContinue:(company:CompanyCard,consents:Consent[])=>void};

const legalForms={ip:'Индивидуальный предприниматель',ooo:'Общество с ограниченной ответственностью'};
const statuses={active:'Действует',liquidating:'В процессе ликвидации',reorganizing:'В процессе реорганизации',liquidated:'Ликвидирована'};

export default function CompanyOnboarding({busy,initialCompany=null,initialConsents=[],initialProgress,onProgressChange,onContinue}:Props){
 const [inn,setInn]=useState(initialProgress?.inn||initialCompany?.inn||''),[company,setCompany]=useState<CompanyCard|null>(initialProgress?.company??initialCompany),[loading,setLoading]=useState(false),[error,setError]=useState('');
 const [demoCompanies,setDemoCompanies]=useState<DemoCompanyOption[]>([]),[selectedDemoCompany,setSelectedDemoCompany]=useState(''),[catalogLoading,setCatalogLoading]=useState(true),[catalogError,setCatalogError]=useState('');
 const [confirmed,setConfirmed]=useState(initialProgress?.confirmed??(!!initialCompany&&initialConsents.length===3)),[bkiCompany,setBkiCompany]=useState(initialProgress?.bkiCompany??initialConsents.some(c=>c.kind==='bki_company')),[bkiOwner,setBkiOwner]=useState(initialProgress?.bkiOwner??initialConsents.some(c=>c.kind==='bki_owner')),[personalData,setPersonalData]=useState(initialProgress?.personalData??initialConsents.some(c=>c.kind==='personal_data'));
 function emitProgress(next:Partial<CompanyStepProgress>){onProgressChange({inn,company,confirmed,bkiCompany,bkiOwner,personalData,...next});}
 const canLookup=/^\d{10}(\d{2})?$/.test(inn);
 const canContinue=!!company&&confirmed&&bkiCompany&&bkiOwner&&personalData&&!busy;

 async function lookupInn(value:string){
  if(!/^\d{10}(\d{2})?$/.test(value)||loading)return;
  setInn(value);setLoading(true);setError('');setCompany(null);setConfirmed(false);setBkiCompany(false);setBkiOwner(false);setPersonalData(false);emitProgress({inn:value,company:null,confirmed:false,bkiCompany:false,bkiOwner:false,personalData:false});
  try{const next=await api<CompanyCard>(`/v2/companies/${value}`);setCompany(next);emitProgress({inn:value,company:next});}
  catch(e){setError((e as Error).message);}
  finally{setLoading(false);}
 }
 async function lookup(event:FormEvent){event.preventDefault();await lookupInn(inn);}

 useEffect(()=>{let active=true;api<DemoCompanyOption[]>('/v2/demo/companies').then(options=>{
  if(!active)return;setDemoCompanies(options);if(!options.length)return;
  const selected=options.find(item=>item.inn===initialCompany?.inn)??options.find(item=>item.inn===initialProgress?.inn)??options[0];
  setSelectedDemoCompany(selected.persona_id);
  if(!initialCompany||initialCompany.inn!==selected.inn)void lookupInn(selected.inn);
 }).catch(e=>{if(active)setCatalogError((e as Error).message);}).finally(()=>{if(active)setCatalogLoading(false);});return()=>{active=false;};},[]);

 function proceed(){
  if(!company||!canContinue)return;
  // SIMPLIFICATION: checkbox confirmation and its timestamp model consent capture in this demo; no legal identity proof is performed.
  const given_at=new Date().toISOString();
  const consents:Consent[]=[
   {kind:'bki_company',subject_inn:company.inn,given_at},
   {kind:'bki_owner',subject_inn:company.owner_inn,given_at},
   {kind:'personal_data',subject_inn:company.owner_inn,given_at},
  ];
  emitProgress({inn:company.inn,company,confirmed:true,bkiCompany:true,bkiOwner:true,personalData:true});onContinue(company,consents);
 }

 const ownerInn=company?.owner_inn||'';
 const maskedOwnerInn=ownerInn.length===12?`${ownerInn.slice(0,4)}••••••${ownerInn.slice(-2)}`:ownerInn;
 const registered=company?new Intl.DateTimeFormat('ru-RU').format(new Date(`${company.registered_at}T00:00:00`)):'';

 return <div className="content v2-onboarding">
  <p className="eyebrow">ЗАЯВКА НА ФИНАНСИРОВАНИЕ</p>
  <h1>Проверим карточку бизнеса<span className="hello-dot">.</span></h1>
  <p className="lead">Введите ИНН, сверьте сведения из реестра и оформите согласия до подачи заявки.</p>
  <div className="v2-steps" aria-label="Этапы заявки">
   <span className="current"><b>1</b> Компания и согласия</span>
   <span><b>2</b> Потребность</span>
  </div>
  {!company?<section className="card v2-lookup-card">
   <span className="icon-tile red"><Search size={22}/></span>
   <h2>{demoCompanies.length?'Выберите компанию для демо':'Введите ИНН бизнеса'}</h2>
   <p className="muted">{demoCompanies.length?'Каталог строится по текущим синтетическим персонам; доступен как минимум один вариант.':'Мы проверим номер в синтетическом реестре демо-системы и покажем найденную карточку.'}</p>
   {catalogLoading&&<p className="v2-hint">Загружаем список компаний…</p>}
   {catalogError&&<p className="v2-error" role="alert">Не удалось загрузить список компаний. Обновите страницу и попробуйте ещё раз.</p>}
   {!catalogLoading&&!catalogError&&<form onSubmit={lookup}>
    {demoCompanies.length>0?<label className="field-label">Синтетическая компания
     <select aria-label="Синтетическая компания" value={selectedDemoCompany} onChange={event=>{const next=demoCompanies.find(item=>item.persona_id===event.target.value);setSelectedDemoCompany(event.target.value);if(next)void lookupInn(next.inn);}}>
      {demoCompanies.map(option=><option key={option.persona_id} value={option.persona_id}>{option.title} · {option.company_name} · ИНН {option.inn}</option>)}
     </select>
    </label>:<label className="field-label">ИНН компании
     <input aria-label="ИНН компании" type="text" inputMode="numeric" autoComplete="off" maxLength={12} value={inn} onChange={e=>{const value=e.target.value.replace(/\D/g,'').slice(0,12);setInn(value);emitProgress({inn:value,company:null,confirmed:false,bkiCompany:false,bkiOwner:false,personalData:false});}} placeholder="10 или 12 цифр"/>
    </label>}
    {demoCompanies.length===0&&<p className="v2-hint">Для ООО — 10 цифр, для ИП — 12. Проверка контрольной суммы здесь не выполняется.</p>}
    {error&&<p className="v2-error" role="alert">{error}</p>}
    <button className="button primary" disabled={demoCompanies.length?loading:!canLookup||loading}>
     {loading?<LoaderCircle className="spin" size={17}/>:<Search size={17}/>} {demoCompanies.length?'Проверить компанию':'Найти компанию'}
    </button>
   </form>}
   <p className="v2-demo-note"><ShieldCheck size={15}/> Демо использует только синтетические данные.</p>
  </section>:<div className="v2-company-grid">
   <section className="card v2-company-card">
    <button className="text-button" onClick={()=>{setCompany(null);setError('');emitProgress({company:null,confirmed:false,bkiCompany:false,bkiOwner:false,personalData:false});}}><ArrowLeft size={16}/> Изменить ИНН</button>
    <div className="v2-section-title"><span className="icon-tile"><Building2 size={21}/></span><div><h2>Данные компании</h2><p>Проверьте карточку перед продолжением</p></div><span className="v2-badge">ДАННЫЕ РЕЕСТРА</span></div>
    <div className="v2-company-name">{company.name}</div>
    <div className="v2-details">
     <div><span>ИНН</span><b>{company.inn}</b></div>
     <div><span>ОГРН / ОГРНИП</span><b>{company.ogrn}</b></div>
     <div><span>Правовая форма</span><b>{legalForms[company.legal_form]}</b></div>
     <div><span>Статус</span><b>{statuses[company.status]}</b></div>
     <div><span>Дата регистрации</span><b>{registered}</b></div>
     <div><span>Основной ОКВЭД</span><b>{company.okved_main}</b></div>
     <div className="wide"><span>Регион / адрес</span><b>{company.region}, {company.address}</b></div>
    </div>
    <div className="v2-owner"><small>Основной собственник для проверки БКИ</small><b>{company.owner_name}</b><span>ИНН {maskedOwnerInn}</span></div>
    <label className="v2-check"><input type="checkbox" checked={confirmed} onChange={e=>{setConfirmed(e.target.checked);emitProgress({confirmed:e.target.checked});}}/><span>Подтверждаю, что данные компании указаны верно.</span></label>
   </section>
   <section className="card v2-consent-card">
    <h2>Согласия</h2>
    <p className="muted">Каждое согласие отмечается отдельно. Оно будет приложено к заявке с ИНН субъекта и временем подтверждения.</p>
    <label className="v2-check"><input type="checkbox" checked={bkiCompany} onChange={e=>{setBkiCompany(e.target.checked);emitProgress({bkiCompany:e.target.checked});}}/><span><b>БКИ компании · 218-ФЗ</b><small>Запрос кредитной истории по ИНН бизнеса {company.inn}.</small></span></label>
    <label className="v2-check"><input type="checkbox" checked={bkiOwner} onChange={e=>{setBkiOwner(e.target.checked);emitProgress({bkiOwner:e.target.checked});}}/><span><b>БКИ собственника · 218-ФЗ</b><small>{company.legal_form==='ip'?'Для ИП — кредитная история предпринимателя.':`Запрос по ИНН основного собственника ${maskedOwnerInn}.`}</small></span></label>
    <label className="v2-check"><input type="checkbox" checked={personalData} onChange={e=>{setPersonalData(e.target.checked);emitProgress({personalData:e.target.checked});}}/><span><b>Персональные данные · 152-ФЗ</b><small>Обработка сведений, необходимых для рассмотрения заявки.</small></span></label>
    <button className="button primary full" disabled={!canContinue} onClick={proceed}>Продолжить к потребности <ArrowRight size={17}/></button>
    <p className="v2-footnote">Продолжение доступно после подтверждения карточки и всех трёх согласий.</p>
   </section>
  </div>}
 </div>;
}
