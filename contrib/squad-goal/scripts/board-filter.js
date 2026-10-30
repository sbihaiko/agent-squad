// The board's Specs and label rules: one text, inlined by the hub into the desktop board and the mobile Board tab. It reads `bo`.
const SP=[["all","every card the other filters leave"],["only","only the cards that carry the spec label"],["hidden","every card but those"]];
const specMode=()=>{const v=bo.specs;if(v===true)return 1;if(v===false||v===undefined||v===null)return 0;
 const n=Math.trunc(+v);return n===1||n===2?n:0};   // a value the page cannot read is all, never a silent hide
const spOk=x=>{const m=specMode();return m===0||(m===1?!!x.sp:!x.sp)};
const labelOk=(sel,mode,x)=>{if(!sel.length)return true;const has=l=>(x.l||[]).includes(l);
 if(mode==="all")return sel.every(has);
 const fam={};sel.forEach(l=>{const k=l.includes(":")?l.split(":")[0]:"";(fam[k]=fam[k]||[]).push(l)});
 return Object.values(fam).every(g=>g.some(has))};
