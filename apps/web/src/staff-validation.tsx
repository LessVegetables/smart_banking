import {useEffect,useState} from 'react';
import {ArrowLeft,RefreshCw,ShieldAlert} from 'lucide-react';
import {api,type ModelValidationReport,type User} from './types';
import './staff-validation.css';

const percent=(value:number|null)=>value===null?'—':`${(value*100).toLocaleString('ru-RU',{maximumFractionDigits:1})}%`;
const decimal=(value:number|null)=>value===null?'—':value.toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:3});
const count=(value:number)=>value.toLocaleString('ru-RU');
const statusNames={stable:'Стабильно',watch:'Наблюдать',shift:'Сдвиг'} as const;
const calibrationNames={within:'В пределах интервала',below:'Прогноз ниже факта',above:'Прогноз выше факта'} as const;

export default function StaffValidation({user,onBack}:{user:User;onBack:()=>void}){
 const [report,setReport]=useState<ModelValidationReport|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 async function load(){setBusy(true);setError('');try{setReport(await api<ModelValidationReport>('/v2/staff/model-validation'));}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 useEffect(()=>{void load();},[user.id]);
 if(user.staff_position!=='risk_manager')return <div className="content"><section className="card staff-validation-error"><ShieldAlert size={25}/><h2>Доступ для риск-менеджера</h2><p>Войдите в рабочее место с должностью «Риск-менеджер», чтобы открыть валидацию модели.</p><button className="button secondary" onClick={onBack}><ArrowLeft size={16}/>К очереди заявок</button></section></div>;
 const holdout=report?.samples.find(sample=>sample.name==='holdout');
 const cap20=report?.cap.reduce((best,point)=>Math.abs(point.companies_share-.2)<Math.abs(best.companies_share-.2)?point:best,report.cap[0]);
 return <div className="content staff-validation-page">
  <div className="staff-validation-heading"><div><p className="eyebrow">РАБОЧЕЕ МЕСТО · РИСК-МЕНЕДЖЕР</p><h1>Валидация скоринговой модели</h1><p className="lead">Качество, калибровка и стабильность карты на отложенной выборке.</p></div><div className="staff-validation-actions"><button className="button secondary" onClick={onBack}><ArrowLeft size={16}/>К очереди</button><button className="button secondary" onClick={()=>void load()} disabled={busy}><RefreshCw size={16} className={busy?'staff-validation-spin':''}/>{busy?'Обновляем…':'Обновить'}</button></div></div>
  {error&&<div className="staff-error" role="alert">{error}</div>}
  {busy&&!report?<section className="card staff-validation-empty">Загружаем отчёт валидации…</section>:report&&<>
   <section className="staff-validation-meta"><span>Карта <b>{report.scorecard_version}</b></span><span>Набор признаков <b>{report.feature_set_version}</b></span><span>Отложенная выборка <b>{count(holdout?.companies??0)} компаний</b></span><span>Срез мира <b>{report.world.as_of}</b> · seed {report.world.seed}, N={count(report.world.n)}</span></section>
   <div className="staff-validation-metrics">
    <article className="card"><small>Gini · отложенная выборка</small><b>{decimal(report.gini.holdout)}</b><span>95% ДИ: {decimal(report.gini.holdout_ci_low)}–{decimal(report.gini.holdout_ci_high)}</span></article>
    <article className="card"><small>Gini · обучающая выборка</small><b>{decimal(report.gini.train)}</b><span>Для сравнения с отложенной выборкой</span></article>
    <article className="card"><small>Потолок истинной PD</small><b>{decimal(report.gini.true_pd_ceiling)}</b><span>Gini по известному риску в мире</span></article>
    <article className="card"><small>Дефолты, пойманные CAP</small><b>{cap20?percent(cap20.defaults_share):'—'}</b><span>При отказе примерно {cap20?percent(cap20.companies_share):'20%'} самых рискованных</span></article>
   </div>
   <section className="card staff-validation-section"><div className="staff-validation-section-title"><div><h2>Калибровка по грейдам</h2><p>Сравнение прогнозной PD с фактической долей дефолтов и 95%-м интервалом Уилсона. Прогноз ниже факта означает, что модель недооценивает риск.</p></div><span>Всего: прогноз {percent(report.calibration.predicted_pd)} · факт {percent(report.calibration.actual_rate)}</span></div>
    <div className="staff-validation-table-wrap"><table className="staff-validation-table"><thead><tr><th>Грейд</th><th>PD модели</th><th>Компании</th><th>Дефолты</th><th>Прогноз PD</th><th>Доля дефолтов · 95% ДИ</th><th>Оценка</th></tr></thead><tbody>{report.calibration.grades.map(grade=><tr key={grade.grade}><th>{grade.grade}</th><td>{percent(grade.pd_min)}–{grade.pd_max===null?'∞':percent(grade.pd_max)}</td><td>{count(grade.companies)} <small>({percent(grade.companies_share)})</small></td><td>{count(grade.defaults)}</td><td>{percent(grade.predicted_pd)}</td><td>{grade.actual_rate===null?'—':`${percent(grade.actual_rate)} · ${percent(grade.actual_ci_low)}–${percent(grade.actual_ci_high)}`}</td><td><span className={'staff-validation-badge '+(grade.predicted_vs_actual||'none')}>{grade.predicted_vs_actual?calibrationNames[grade.predicted_vs_actual]:'Нет данных'}</span></td></tr>)}</tbody></table></div>
   </section>
   <section className="staff-validation-stability"><div className="staff-validation-section-title"><div><h2>Стабильность распределений</h2><p>PSI сравнивает обучающую и отложенную выборки; по признакам показан CSI.</p></div><span>Пороги: {report.thresholds.psi_watch} · {report.thresholds.psi_shift}</span></div>
    <div className="staff-validation-psi">{[report.psi_grades,report.psi_points,...report.psi_features].map(item=><article className="card" key={item.name}><small>{item.label}</small><b>{decimal(item.value)}</b><span className={'staff-validation-badge '+item.status}>{statusNames[item.status]}</span></article>)}</div>
   </section>
   <section className="card staff-validation-section"><div className="staff-validation-section-title"><div><h2>Ограничения валидации</h2><p>Учитывайте эти ограничения при интерпретации метрик.</p></div></div><ul className="staff-validation-limitations">{report.limitations.map((limitation,index)=><li key={`${index}-${limitation}`}>{limitation}</li>)}</ul></section>
   <p className="staff-validation-footnote">Синтетическая учебная модель. Эти результаты показывают работу конвейера валидации и не подтверждают риск реального кредитного портфеля. В выборке {count(holdout?.companies??0)} компаний, дефолтность {percent(holdout?.default_rate??0)}.</p>
  </>}
 </div>;
}
