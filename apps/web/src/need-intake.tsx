import {useEffect,useLayoutEffect,useRef,useState,type FormEvent,type InputHTMLAttributes} from 'react';
import {ArrowLeft,ArrowRight,Check,Package,Wallet} from 'lucide-react';
import {productNames,type EquipmentInfo,type NeedRequest,type NeededBy,type ProductId,type Purpose} from './types';
import './need-intake.css';

export interface NeedDraft {
 purpose:Purpose|'';amount:string;term:string;needed_by:NeededBy|'';
 equipment_description:string;equipment_price:string;equipment_down_payment:string;equipment_supplier_inn:string;
}

type Props={busy:boolean;initialValue:NeedDraft;onDraftChange:(draft:NeedDraft)=>void;onSubmit:(request:NeedRequest)=>void;companyName:string;companyInn:string;onBack:()=>void};

const purposeOptions:{id:Purpose;label:string;hint:string}[]=[
 {id:'inventory',label:'Закупить товар или сырьё',hint:'Пополнить запасы для работы'},
 {id:'payroll_rent',label:'Выплатить зарплату или аренду',hint:'Покрыть текущие расходы'},
 {id:'equipment',label:'Купить оборудование',hint:'Обновить или расширить мощности'},
 {id:'cash_gap',label:'Закрыть кассовый разрыв',hint:'Переждать задержку поступлений'},
 {id:'seasonal_gap',label:'Подготовиться к сезону',hint:'Заранее профинансировать сезонный рост'},
 {id:'contract',label:'Выполнить контракт',hint:'Оплатить расходы до поступления оплаты'},
 {id:'other',label:'Другая задача',hint:'Расскажите, сколько и на какой срок нужно'},
];
const recommended:Record<Purpose,ProductId>={inventory:'working_capital',payroll_rent:'working_capital',equipment:'investment',cash_gap:'overdraft',seasonal_gap:'credit_line',contract:'credit_line',other:'working_capital'};
// Ruble amounts: the draft keeps the raw number ("1000000.50"), the field shows it grouped ("1 000 000.50").
const groupDigits=(raw:string)=>{const [whole,fraction]=raw.split('.');const grouped=whole.replace(/\B(?=(\d{3})+(?!\d))/g,' ');return fraction===undefined?grouped:`${grouped}.${fraction}`;};
function rawAmount(text:string){
 let s=text.replace(/,/g,'.').replace(/[^\d.]/g,'');const dot=s.indexOf('.');
 if(dot>=0)s=s.slice(0,dot+1)+s.slice(dot+1).replace(/\./g,'').slice(0,2);
 return s.replace(/^0+(?=\d)/,'').replace(/^\./,'0.');
}
function AmountInput({value,onValue,...rest}:{value:string;onValue:(raw:string)=>void}&Omit<InputHTMLAttributes<HTMLInputElement>,'value'|'onChange'|'type'>){
 const ref=useRef<HTMLInputElement>(null),caret=useRef<number|null>(null);
 const shown=groupDigits(value);
 // Reformatting moves the caret to the end: put it back after the same number of digits.
 useLayoutEffect(()=>{const input=ref.current,wanted=caret.current;caret.current=null;if(!input||wanted===null||document.activeElement!==input)return;
  let pos=0,seen=0;while(pos<shown.length&&seen<wanted){if(shown[pos]!==' ')seen++;pos++;}input.setSelectionRange(pos,pos);});
 return <input {...rest} ref={ref} type="text" inputMode="decimal" value={shown} onChange={e=>{const input=e.target;caret.current=rawAmount(input.value.slice(0,input.selectionStart??input.value.length)).length;onValue(rawAmount(input.value));}}/>;
}

const emptyDraft:NeedDraft={purpose:'',amount:'',term:'',needed_by:'',equipment_description:'',equipment_price:'',equipment_down_payment:'',equipment_supplier_inn:''};

export default function NeedIntake({busy,initialValue,onDraftChange,onSubmit,companyName,companyInn,onBack}:Props){
 const [draft,setDraft]=useState<NeedDraft>({...emptyDraft,...initialValue});
 useEffect(()=>setDraft({...emptyDraft,...initialValue}),[initialValue]);
 function update<K extends keyof NeedDraft>(key:K,value:NeedDraft[K]){const next={...draft,[key]:value};setDraft(next);onDraftChange(next);}
 const equipment=draft.purpose==='equipment';
 const amount=Number(draft.amount),term=Number(draft.term),price=Number(draft.equipment_price),downPayment=Number(draft.equipment_down_payment);
 const equipmentValid=!equipment||(draft.equipment_description.trim().length>0&&price>0&&downPayment>=0&&downPayment<=price&&/^\d{10}(\d{2})?$/.test(draft.equipment_supplier_inn));
 const valid=!!draft.purpose&&amount>0&&Number.isInteger(amount)&&Number.isInteger(term)&&term>=1&&term<=120&&!!draft.needed_by&&equipmentValid;
 function submit(e:FormEvent){e.preventDefault();if(!valid||busy||!draft.purpose||!draft.needed_by)return;
  const product=recommended[draft.purpose];
  const equipmentInfo:EquipmentInfo|null=equipment?{description:draft.equipment_description.trim(),price,down_payment:downPayment,supplier_inn:draft.equipment_supplier_inn}:null;
  onSubmit({purpose:draft.purpose,amount,term_months:term,needed_by:draft.needed_by,preferred_product:product,declared_annual_revenue:null,equipment:equipmentInfo});
 }
 return <div className="content v2-onboarding need-intake">
  <p className="eyebrow">ШАГ 2 ИЗ 2 · ПОТРЕБНОСТЬ В ФИНАНСИРОВАНИИ</p><h1>На что нужны деньги?</h1>
  <p className="lead">Ответьте на несколько вопросов — подберём подходящий формат для вашего бизнеса.</p>
  <div className="need-company"><span className="need-company-icon"><Wallet size={18}/></span><div><b>{companyName}</b><small>ИНН {companyInn} · карточка и согласия сохранены в этой вкладке</small></div></div>
  <form onSubmit={submit} className="need-form">
   <section className="card need-section"><div className="need-section-heading"><span>01</span><div><h2>Цель финансирования</h2><p>Выберите задачу бизнеса</p></div></div>
    <div className="need-purpose-grid">{purposeOptions.map((option,i)=><button type="button" key={option.id} className={'need-purpose '+(draft.purpose===option.id?'selected':'')} onClick={()=>update('purpose',option.id)}><small>0{i+1}</small><b>{option.label}</b><span>{option.hint}</span><i>{draft.purpose===option.id?<Check size={15}/>:null}</i></button>)}</div>
    {draft.purpose&&<div className="need-recommendation"><Package size={18}/><span>Подходящий продукт</span><b>{productNames[recommended[draft.purpose]]}</b></div>}
   </section>
   <section className="card need-section"><div className="need-section-heading"><span>02</span><div><h2>Сумма и срок</h2><p>Укажите желаемые параметры</p></div></div>
    <div className="need-fields two"><label className="field-label">Сколько нужно<AmountInput value={draft.amount} onValue={value=>update('amount',value)} placeholder="Например, 1 000 000"/><small>В рублях</small>{amount>0&&!Number.isInteger(amount)&&<p className="need-validation" role="alert">Укажите сумму в целых рублях, без копеек.</p>}</label>
     <label className="field-label">На какой срок<input type="number" min="1" max="120" step="1" inputMode="numeric" value={draft.term} onChange={e=>update('term',e.target.value)} placeholder="Например, 24"/><small>Месяцев, от 1 до 120</small></label></div>
   </section>
   <section className="card need-section"><div className="need-section-heading"><span>03</span><div><h2>Когда нужны деньги?</h2><p>Это поможет понять срочность запроса</p></div></div>
    <div className="need-time-options">{([['days','В ближайшие дни'],['week','В течение недели'],['month','В течение месяца'],['later','Позже']] as [NeededBy,string][]).map(([value,label])=><button type="button" key={value} className={'need-time '+(draft.needed_by===value?'selected':'')} onClick={()=>update('needed_by',value)}>{label}{draft.needed_by===value&&<Check size={15}/>}</button>)}</div>
   </section>
   {equipment&&<section className="card need-section"><div className="need-section-heading"><span>04</span><div><h2>Что планируете купить?</h2><p>Эти данные нужны для инвестиционного предложения</p></div></div>
    <div className="need-fields"><label className="field-label">Оборудование<input value={draft.equipment_description} onChange={e=>update('equipment_description',e.target.value)} placeholder="Например, производственный станок"/></label>
     <div className="need-fields two"><label className="field-label">Стоимость оборудования<input type="number" min="1" step="1" value={draft.equipment_price} onChange={e=>update('equipment_price',e.target.value)} placeholder="В рублях"/></label><label className="field-label">Собственный взнос<input type="number" min="0" step="1" value={draft.equipment_down_payment} onChange={e=>update('equipment_down_payment',e.target.value)} placeholder="В рублях"/></label></div>
     <label className="field-label">ИНН поставщика<input inputMode="numeric" maxLength={12} value={draft.equipment_supplier_inn} onChange={e=>update('equipment_supplier_inn',e.target.value.replace(/\D/g,'').slice(0,12))} placeholder="10 или 12 цифр"/></label>
     {downPayment>price&&<p className="need-validation" role="alert">Собственный взнос не может быть больше стоимости оборудования.</p>}
    </div>
   </section>}
   <div className="need-actions"><button type="button" className="button secondary" onClick={onBack}><ArrowLeft size={16}/>К карточке компании</button><button className="button primary" disabled={!valid||busy}>{busy?'Отправляем…':'Перейти к заявке'} <ArrowRight size={17}/></button></div>
  </form>
 </div>;
}

export {emptyDraft};
