export type Answers=Record<string,any>;
export interface Question {id:string;section:string;title:string;type:string;hint:string;min?:number;max?:number;suffix?:string;max_length?:number;options:{value:string;label:string}[]}
export interface Survey {id:string;version:number;answers:Answers;questions:Question[];complete:boolean;all_questions_count:number}
export interface Product {id:string;name:string;short:string;min:number;max:number;min_term:number;max_term:number;annual_rate:number}
export interface Offer {product_id:string;name:string;amount:number;term:number;annual_rate:number;monthly_payment:number;expected_payment:number|null;limits:{code:string;label:string;amount:number}[];binding:string;model:string;alternative:boolean;requires_collateral:boolean}
export interface Decision {status:string;score:number|null;risk:string|null;reasons:string[];reason_codes:string[];offers:Offer[];factors:{code:string;label:string;points:number}[];checks:{source:string;status:string;message:string}[];documents:string[];rule_version:string;at?:string}
export interface Application {id:string;revision:number;status:string;name:string;answers:Answers;created:string;events:{at:string;status:string;message:string}[];decision:Decision|null;selected_offer:Offer|null;offer_expires:number|null;documents:{id:string;name:string;size:number;status:string}[]}
export interface User {id:string;name:string;role:string}
export interface Persona {id:string;title:string;subtitle:string}
export interface CompanyCard {inn:string;ogrn:string;legal_form:'ip'|'ooo';name:string;status:'active'|'liquidating'|'reorganizing'|'liquidated';registered_at:string;okved_main:string;region:string;address:string;owner_name:string;owner_inn:string}
export interface Consent {kind:'bki_company'|'bki_owner'|'personal_data';subject_inn:string;given_at:string}
export async function api<T>(path:string,method='GET',body?:unknown):Promise<T>{
 const res=await fetch('/api'+path,{method,credentials:'same-origin',headers:body instanceof FormData?{}:body?{'Content-Type':'application/json'}:{},body:body instanceof FormData?body:body?JSON.stringify(body):undefined});
 if(!res.ok){let text='Не удалось выполнить запрос';try{const j=await res.json();text=typeof j.detail==='string'?j.detail:'Проверьте заполненные поля';}catch{if(res.status===502)text='Сервер пока недоступен. Проверьте запуск API.';}throw new Error(text);}
 return res.json();
}
export const money=(n:number)=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(n)+' ₽';
export const compact=(n:number)=>n>=1000000?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(n/1000000)+' млн ₽':money(n);
export const statuses:Record<string,string>={submitted:'Заявка подана',checking:'Проверяем данные',scoring:'Рассчитываем условия',approved:'Предварительно одобрено',counteroffer:'Есть предложение',needs_data:'Нужны документы',manual_review:'На рассмотрении',declined:'Сейчас недоступно',signing:'Ожидает подписания',issued:'Выдача смоделирована',closed:'Заявка закрыта'};
