import test from 'node:test';
import assert from 'node:assert/strict';
import {chooseSnapshot,snapshotChanged} from '../src/archiveSelection.ts';

const snapshot=(date,mode,revision=1)=>({trade_date:date,mode,revision});
const old={date:'2026-11-02',mode:'close'};

test('new day noon becomes latest ahead of the prior close',()=>{
 assert.deepEqual(chooseSnapshot([snapshot('2026-11-02','close'),snapshot('2026-11-03','midday')],old,true),{date:'2026-11-03',mode:'midday'});
});
test('same-day close replaces noon regardless of response order or revision',()=>{
 const noon={date:'2026-11-03',mode:'midday'};
 const archives=[snapshot('2026-11-03','close',2),snapshot('2026-11-03','midday',99)];
 assert.deepEqual(chooseSnapshot(archives,noon,true),{date:noon.date,mode:'close'});
 assert.deepEqual(chooseSnapshot(archives.toReversed(),noon,true),{date:noon.date,mode:'close'});
});
test('manual date or mode remains pinned through polling and manual refresh',()=>{
 const pinned={date:'2026-11-03',mode:'midday'};
 const archives=[snapshot('2026-11-04','close'),snapshot('2026-11-03','close')];
 assert.equal(chooseSnapshot(archives,pinned,false),pinned);
 assert.equal(chooseSnapshot(archives,old,false),old);
});
test('latest shortcut can resume follow and change same-day mode',()=>{
 const pinned={date:'2026-11-03',mode:'midday'};
 const next=chooseSnapshot([snapshot(pinned.date,'close')],pinned,true);
 assert.equal(snapshotChanged(pinned,next),true);
 assert.equal(next.mode,'close');
});
test('revision-only refresh preserves selection and open revision-scoped details',()=>{
 const next=chooseSnapshot([snapshot(old.date,'close',100)],old,true);
 assert.equal(snapshotChanged(old,next),false);
});
test('empty or unsupported archives cannot erase a selected snapshot',()=>{
 assert.equal(chooseSnapshot([],old,true),old);
 assert.equal(chooseSnapshot([snapshot('2026-11-04','live')],old,true),old);
});
