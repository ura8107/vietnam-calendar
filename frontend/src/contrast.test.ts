import {describe,expect,it} from 'vitest';
const luminance=(hex:string)=>{const channels=[1,3,5].map(index=>parseInt(hex.slice(index,index+2),16)/255).map(value=>value<=.04045?value/12.92:((value+.055)/1.055)**2.4);return .2126*channels[0]+.7152*channels[1]+.0722*channels[2]};
const contrast=(left:string,right:string)=>{const [high,low]=[luminance(left),luminance(right)].sort((a,b)=>b-a);return (high+.05)/(low+.05)};
describe('WCAG foreground/background pairs',()=>{it.each([['body ink','#202b28','#f2f0e9'],['paper ink','#202b28','#fffdf8'],['jade button','#ffffff','#246c5b'],['red required badge','#ffffff','#9e2f2b']])('%s is at least AA',(_name,foreground,background)=>expect(contrast(foreground,background)).toBeGreaterThanOrEqual(4.5))});
