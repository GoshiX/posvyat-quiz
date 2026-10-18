import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import sharp from 'sharp';

const root = resolve('data/group-video-preparation');
const folder = resolve(root, 'rendered');
const records = readdirSync(folder).filter(name => /--[a-f0-9]+\.json$/.test(name)).map(name => JSON.parse(readFileSync(resolve(folder,name)))).sort((a,b)=>a.number.localeCompare(b.number));
const cellWidth=320, cellHeight=208, columns=5;
const layers=[];
for (const [index,record] of records.entries()) {
  const frame = execFileSync('ffmpeg',['-v','error','-threads','1','-filter_threads','1','-ss',String(record.requested_duration_seconds/2),'-i',record.mp4.path,'-frames:v','1','-vf','scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2','-f','image2pipe','-c:v','png','-'],{maxBuffer:4*1024*1024});
  const label=Buffer.from(`<svg width="320" height="28"><rect width="320" height="28" fill="#202226"/><text x="12" y="20" font-family="sans-serif" font-size="16" fill="white">${record.number} · ${record.requested_duration_seconds} s · 30 fps</text></svg>`);
  const input=await sharp(frame).extend({top:0,bottom:28,left:0,right:0,background:'#202226'}).composite([{input:label,left:0,top:180}]).png().toBuffer();
  layers.push({input,left:index%columns*cellWidth,top:Math.floor(index/columns)*cellHeight});
}
await sharp({create:{width:columns*cellWidth,height:Math.ceil(records.length/columns)*cellHeight,channels:3,background:'#101114'}}).composite(layers).png().toFile(resolve(root,'contact-sheet.png'));
console.log(`Contact sheet: ${records.length} groups`);
