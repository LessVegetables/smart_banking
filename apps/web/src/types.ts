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
export interface DemoCompanyOption {persona_id:string;title:string;story:string;inn:string;legal_form:'ip'|'ooo';company_name:string}
export interface Consent {kind:'bki_company'|'bki_owner'|'personal_data';subject_inn:string;given_at:string}
export type Purpose='inventory'|'payroll_rent'|'equipment'|'cash_gap'|'seasonal_gap'|'contract'|'other';
export type NeededBy='days'|'week'|'month'|'later';
export type ProductId='overdraft'|'working_capital'|'investment'|'credit_line';
export interface EquipmentInfo {description:string;price:number;down_payment:number;supplier_inn:string}
export interface NeedRequest {purpose:Purpose;amount:number;term_months:number;needed_by:NeededBy;preferred_product:ProductId;declared_annual_revenue:null;equipment:EquipmentInfo|null}
export interface V2StoredRequest extends NeedRequest {inn:string;legal_form:'ip'|'ooo'}
export interface V2SourceProgress {at:string;source:string;attempt:number;status:string;final:boolean}
export interface V2LimitConstraint {name:'cash_flow'|'leverage'|'collateral'|'turnover'|'product_max'|'requested';value:number|null;inputs:Record<string,unknown>;note:string|null}
export interface V2Offer {offer_id:string;kind:string;product:string;amount:number;term_months:number;rate:number;monthly_payment:number;collateral:string[];guarantee_fee:number;disbursement:string;matches_request:boolean;tradeoff:string;limit:{constraints:V2LimitConstraint[];binding:string;limit:number};valid_until:string}
export interface V2ClientDecision {status:string;matches_request:boolean|null;route_messages:string[];reasons:string[];offers:V2Offer[];counterfactuals:{client_text:string;resulting_amount:number|null;resulting_rate:number|null}[];data_quality:{tier:string;client_message:string}|null;requested_documents:string[];reapply_after:string|null;decided_at:string}
export interface V2ClosingDocument {doc_id:string;title:string;signer:string;text:string;signed_at?:string|null}
export interface V2ClosingCondition {code:string;title:string;status:'pending'|'met'|'failed'}
export interface V2Application {id:string;status:string;status_text:string;revision:number;request:V2StoredRequest;created:string;updated:string;decision:V2ClientDecision|null;events:{at:string;status:string;message:string}[];sources:V2SourceProgress[];selected_offer_id:string|null;closing?:{offer_id:string;documents:V2ClosingDocument[];code:{expires_at:string;attempts_left:number}|null;signature:unknown;conditions:V2ClosingCondition[];payout:{text:string}|null}|null}
export interface V2PreApproval {product:string;term_months:number;amount:number;rate:number;valid_until:string;client_text:string}
export async function api<T>(path:string,method='GET',body?:unknown):Promise<T>{
 const res=await fetch('/api'+path,{method,credentials:'same-origin',headers:body instanceof FormData?{}:body?{'Content-Type':'application/json'}:{},body:body instanceof FormData?body:body?JSON.stringify(body):undefined});
 if(!res.ok){let text='Не удалось выполнить запрос';try{const j=await res.json();text=typeof j.detail==='string'?j.detail:'Проверьте заполненные поля';}catch{if(res.status===502)text='Сервер пока недоступен. Проверьте запуск API.';}throw new Error(text);}
 return res.json();
}
export const money=(n:number)=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(n)+' ₽';
export const compact=(n:number)=>n>=1000000?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(n/1000000)+' млн ₽':money(n);
export const statuses:Record<string,string>={submitted:'Заявка подана',checking:'Проверяем данные',scoring:'Рассчитываем условия',approved:'Предварительно одобрено',counteroffer:'Есть предложение',needs_data:'Нужны документы',manual_review:'На рассмотрении',declined:'Сейчас недоступно',signing:'Ожидает подписания',issued:'Выдача смоделирована',closed:'Заявка закрыта'};
