import {test} from 'node:test';
import assert from 'node:assert/strict';
import {questionResult,quizTotal,audienceTotals} from './scoring.mjs';

test('strict plurality wins without absolute majority; ties never win',()=>{
  assert.deepEqual(questionResult([4,3,2,1],0,()=>3),{counts:[4,3,2,1],choice:0,won:true});
  assert.equal(questionResult([4,4,0,0],0,()=>2).won,false);
  assert.equal(questionResult([4,4,0,0],0,()=>2).choice,2);
  assert.equal(questionResult([0,0,0,0],0,()=>1).won,false);
  assert.equal(questionResult([1,8,2,0],0,()=>1).choice,1);
});
test('floor applies to the sum of all quiz wins',()=>{
  assert.equal(quizTotal(1),1);assert.equal(quizTotal(2),3);assert.equal(quizTotal(3),4);assert.equal(quizTotal(4),6);
});
test('audience sums equal-weight unrounded group means and trims zeros as values',()=>{
  const groups=['a','b','c','d','e','f'].map(id=>({id}));
  const participants=[{id:'b1',groupId:'b'},{id:'b2',groupId:'b'},{id:'c1',groupId:'c'},{id:'d1',groupId:'d'},{id:'e1',groupId:'e'}];
  const ballots=[{participantId:'b1',ratings:{a:8}},{participantId:'b2',ratings:{a:9}},{participantId:'c1',ratings:{a:10}},{participantId:'d1',ratings:{a:4}},{participantId:'e1',ratings:{a:7}}];
  const result=audienceTotals(groups,participants,ballots,1).find(x=>x.groupId==='a');
  assert.equal(result.score,19.5); // Drop absent f=0 and c=10, sum 4+7+8.5.
  assert.deepEqual(result.retained,['d','e','b']);
});
test('audience with no submissions is zero, without counting own group',()=>{
  const result=audienceTotals([{id:'a'},{id:'b'},{id:'c'}],[],[],0);
  assert.equal(result[0].score,0);assert.equal(result[0].contributions.length,2);
});
