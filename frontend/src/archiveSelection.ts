export type SnapshotMode='close'|'midday';
export type SnapshotChoice={date:string;mode:SnapshotMode};
export type ArchiveSnapshot={trade_date:string;mode:string;revision:number};

/** Follow published archives only; explicit history selections stay pinned. */
export function chooseSnapshot(snapshots:ArchiveSnapshot[],current:SnapshotChoice,followLatest:boolean):SnapshotChoice{
 if(!followLatest)return current;
 const available=snapshots.filter(s=>s.mode==='close'||s.mode==='midday');
 if(!available.length)return current;
 const date=available.reduce((latest,s)=>s.trade_date>latest?s.trade_date:latest,'');
 return {date,mode:available.some(s=>s.trade_date===date&&s.mode==='close')?'close':'midday'};
}

export function snapshotChanged(current:SnapshotChoice,next:SnapshotChoice){
 return current.date!==next.date||current.mode!==next.mode;
}
