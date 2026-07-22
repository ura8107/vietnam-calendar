export type View='calendar'|'review'|'operations';
export type State={view:View;month:string;date:string;q:string;importance:string;category:string;dateFrom:string;dateTo:string;publisher:string;eventId:string};

function ictToday(now=new Date()):string{
  const parts=new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Ho_Chi_Minh',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now);
  const get=(type:string)=>parts.find(part=>part.type===type)?.value||'';
  return `${get('year')}-${get('month')}-${get('day')}`;
}
export function validMonth(value:string|null):value is string{return !!value&&/^\d{4}-(0[1-9]|1[0-2])$/.test(value)}
export function validDate(value:string|null):value is string{
  if(!value||!/^\d{4}-(0[1-9]|1[0-2])-([0-2]\d|3[01])$/.test(value))return false;
  const date=new Date(`${value}T00:00:00Z`);return !Number.isNaN(date.valueOf())&&date.toISOString().slice(0,10)===value;
}
export function normalizeState(url=new URL(location.href),now=new Date()):State{
  const rawDate=url.searchParams.get('date'),rawMonth=url.searchParams.get('month');
  let month=validMonth(rawMonth)?rawMonth:(validDate(rawDate)?rawDate.slice(0,7):ictToday(now).slice(0,7));
  let date=validDate(rawDate)?rawDate:`${month}-01`;
  if(date.slice(0,7)!==month)date=`${month}-01`;
  const rawImportance=url.searchParams.get('importance')||'',importance=['high','middle_high','middle','low'].includes(rawImportance)?rawImportance:'';
  return{view:(['calendar','review','operations'].includes(url.searchParams.get('view')||'')?url.searchParams.get('view'):'calendar') as View,month,date,q:(url.searchParams.get('q')||'').slice(0,200),importance,category:(url.searchParams.get('category')||'').slice(0,60),dateFrom:validDate(url.searchParams.get('from'))?url.searchParams.get('from')!:'',dateTo:validDate(url.searchParams.get('to'))?url.searchParams.get('to')!:'',publisher:(url.searchParams.get('publisher')||'').slice(0,100),eventId:url.searchParams.get('event')||''};
}
export const readState=normalizeState;
export function writeState(state:State,replace=false){
  const normalized=normalizeState(new URL(`http://local/?${new URLSearchParams({view:state.view,month:state.month,date:state.date,q:state.q,importance:state.importance,category:state.category,from:state.dateFrom,to:state.dateTo,publisher:state.publisher,event:state.eventId})}`));
  const url=new URL(location.href);for(const key of ['view','month','date','q','importance','category','from','to','publisher','event'])url.searchParams.delete(key);
  const values={view:normalized.view,month:normalized.month,date:normalized.date,q:normalized.q,importance:normalized.importance,category:normalized.category,from:normalized.dateFrom,to:normalized.dateTo,publisher:normalized.publisher,event:normalized.eventId};
  Object.entries(values).forEach(([key,value])=>{if(value)url.searchParams.set(key,value)});history[replace?'replaceState':'pushState']({},'',url);
}
export function shiftMonth(month:string,delta:number){const [y,m]=month.split('-').map(Number);const d=new Date(Date.UTC(y,m-1+delta,1));return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}`}
