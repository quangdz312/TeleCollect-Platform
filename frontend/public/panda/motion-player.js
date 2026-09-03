import { pandaMotion } from './panda-motion.js';

const smootherstep = u => u*u*u*(u*(u*6-15)+10);
function lookup(path, u) {
  const x = Math.max(0,Math.min(1,u))*(path.length-1);
  const i = Math.min(Math.floor(x),path.length-2);
  const f = x-i;
  return path[i].map((q,j) => q+(path[i+1][j]-q)*f);
}

// Both halves use the very same baked path in reverse; the loop cannot drift.
export function sampleCycle(seconds) {
  const phase=((seconds%18)+18)%18;
  const fromB=phase>=9;
  const t=phase%9;
  const source=pandaMotion.paths[fromB?'liftB':'liftA'];
  const destination=pandaMotion.paths[fromB?'liftA':'liftB'];
  let pose,grip;
  if(t<1.4) { pose=lookup(source,1-smootherstep(t/1.4)); grip=1; }
  else if(t<2) { pose=source[0]; grip=1-smootherstep((t-1.4)/.6); }
  else if(t<3.2) { pose=lookup(source,smootherstep((t-2)/1.2)); grip=0; }
  else if(t<5.7) {
    const s=smootherstep((t-3.2)/2.5);
    pose=lookup(pandaMotion.paths.transfer,fromB?1-s:s); grip=0;
  }
  else if(t<7) { pose=lookup(destination,1-smootherstep((t-5.7)/1.3)); grip=0; }
  else if(t<7.6) { pose=destination[0]; grip=smootherstep((t-7)/.6); }
  else { pose=lookup(destination,smootherstep((t-7.6)/1.4)); grip=1; }
  return {pose,grip,carrying:t>=2&&t<7,restingAtB:t>=7?!fromB:fromB};
}
