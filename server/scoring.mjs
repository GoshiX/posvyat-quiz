export function questionResult(counts, correctIndex, chooseWrong) {
  const max=Math.max(...counts), leaders=counts.flatMap((n,i)=>n===max?[i]:[]);
  const won=counts[correctIndex]>Math.max(...counts.filter((_,i)=>i!==correctIndex));
  const choice=leaders.length===1?leaders[0]:chooseWrong(correctIndex);
  return {counts,choice,won};
}
export const quizTotal = wins => Math.floor(wins*1.5);
export function audienceTotals(groups, participants, ballots, trimCount) {
  const members=new Map(participants.map(p=>[p.id,p.groupId]));
  const bySource=new Map(groups.map(g=>[g.id,[]]));
  for(const b of ballots) { const source=members.get(b.participantId); if(bySource.has(source)) bySource.get(source).push(b.ratings); }
  return groups.map(g=>{
    const contributions=groups.filter(source=>source.id!==g.id).map(source=>{
      const votes=bySource.get(source.id);
      return {groupId:source.id,value:votes.length?votes.reduce((sum,ratings)=>sum+ratings[g.id],0)/votes.length:0,voterCount:votes.length};
    });
    const sorted=[...contributions].sort((a,b)=>a.value-b.value);
    const kept=sorted.slice(trimCount,sorted.length-trimCount);
    return {groupId:g.id,score:kept.reduce((sum,x)=>sum+x.value,0),contributions,retained:kept.map(x=>x.groupId),voterCount:bySource.get(g.id).length};
  });
}
