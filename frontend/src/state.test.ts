import {describe,expect,it} from 'vitest';import {normalizeState,shiftMonth,validDate} from './state';
describe('URL state normalization',()=>{
  it('restores valid filters',()=>{const s=normalizeState(new URL('http://localhost/?view=review&month=2026-07&date=2026-07-18&q=bank&importance=high'));expect(s).toMatchObject({view:'review',month:'2026-07',date:'2026-07-18',q:'bank',importance:'high'})});
  it('defaults a month-only link inside that month',()=>expect(normalizeState(new URL('http://localhost/?month=2026-02')).date).toBe('2026-02-01'));
  it('synchronizes an out-of-month date and rejects impossible dates',()=>{expect(normalizeState(new URL('http://localhost/?month=2026-02&date=2026-03-20')).date).toBe('2026-02-01');expect(validDate('2026-02-30')).toBe(false)});
  it('retains event deep links',()=>expect(normalizeState(new URL('http://localhost/?view=calendar&month=2026-07&event=abc')).eventId).toBe('abc'));
  it('restores combined date range and publisher filters',()=>expect(normalizeState(new URL('http://localhost/?month=2026-07&from=2026-07-01&to=2026-07-31&publisher=Tuoi%20Tre'))).toMatchObject({dateFrom:'2026-07-01',dateTo:'2026-07-31',publisher:'Tuoi Tre'}));
  it('drops unsupported importance values',()=>expect(normalizeState(new URL('http://localhost/?month=2026-07&importance=urgent')).importance).toBe(''));
  it('shifts across year',()=>{expect(shiftMonth('2026-01',-1)).toBe('2025-12');expect(shiftMonth('2026-12',1)).toBe('2027-01')});
});
