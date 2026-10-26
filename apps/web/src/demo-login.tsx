import {useState,type FormEvent} from 'react';
import {ArrowRight,LoaderCircle,LogIn,ShieldCheck} from 'lucide-react';
import './demo-login.css';

type Props={busy:boolean;onSubmit:(email:string)=>void;onStaff:()=>void};

// Display name for a demo profile: the part of the e-mail before "@", or the legacy demo name as is.
export function displayName(name:string){return name.includes('@')?name.split('@')[0]:name;}

export default function DemoLogin({busy,onSubmit,onStaff}:Props){
 const [email,setEmail]=useState('');
 const value=email.trim().toLowerCase();
 // The backend keeps demo profile names up to 80 characters.
 const valid=/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)&&value.length<=80;
 function submit(event:FormEvent){event.preventDefault();if(valid&&!busy)onSubmit(value);}
 return <div className="content demo-login">
  <span className="brand"><span className="eta-mark">η<span/></span><div>Эта<span>Бизнес</span></div></span>
  <section className="card v2-lookup-card">
   <span className="icon-tile red"><LogIn size={22}/></span>
   <p className="eyebrow">ВХОД В КАБИНЕТ</p>
   <h2>Войдите по e-mail</h2>
   {/* SIMPLIFICATION: demo login takes any e-mail with no password or confirmation code; the e-mail is the stable demo profile (see /api/auth/demo). */}
   <p className="muted">Это демо: пароль и код подтверждения не нужны. Тот же e-mail откроет ту же историю заявок.</p>
   <form onSubmit={submit}>
    <label className="field-label">E-mail
     <input aria-label="E-mail" type="email" inputMode="email" autoComplete="email" autoFocus maxLength={80} value={email} onChange={e=>setEmail(e.target.value)} placeholder="name@company.ru"/>
    </label>
    <button className="button primary full" disabled={!valid||busy}>{busy?<LoaderCircle className="spin" size={17}/>:null}Войти <ArrowRight size={17}/></button>
   </form>
   <p className="v2-demo-note"><ShieldCheck size={15}/> Демо использует только синтетические данные.</p>
  </section>
  <button className="text-button" onClick={onStaff}>Вход для сотрудников банка <ArrowRight size={16}/></button>
 </div>;
}
