function renderCore(){
 const ranking=D.tenDay,status=D.sectionStatus?.tenDay;
 const rows=globalThis.KplTracking.front(D,state.theme);
 const topCount=rows.filter(s=>s.tenRank).length;
 $('[data-core-scope]').textContent=`${rows.length}只 · 含10日榜${topCount}只`;
 $('[data-core-head]').innerHTML=['个股 / 身位','今日涨幅','10日涨幅','成交额'].map(v=>`<th>${v}</th>`).join('');
 $('[data-core]').innerHTML=rows.map(s=>`<tr class="${s.code===state.stock?'is-active':''}"><td><button data-stock="${s.code}" title="${esc(s.theme)}"><strong>${esc(s.name)}</strong><span>${s.code}</span><span class="kt-core-labels">${[s.boards?s.boards+'板':'',s.tenRank?'10日第'+s.tenRank+'名':''].filter(Boolean).join(' · ')}</span></button></td><td class="${color(s.quote?.change)}">${pct(s.quote?.change)}</td><td class="${color(s.return_10d)}">${pct(s.return_10d)}</td><td>${money(s.quote?.amount)}</td></tr>`).join('')||'<tr><td colspan="4" class="kt-empty">当前板块暂无前排观察候选</td></tr>';
 const quoteFailed=rows.some(s=>D.sectionStatus?.['quote_'+s.code]?.ok===false);
 $('[data-core-note]').textContent=ranking
  ? `${state.theme} · 日内前排在前，10日涨幅前5（含ST）随后，重复只计一次。可在列表内向下滚动查看。${state.theme!=='全市场'?'当前按板块筛选，点击“全市场”查看全部5只。':''}10日区间 ${ranking.start.slice(5)}—${ranking.end.slice(5)}；${status&&!status.ok?'排名本轮未更新，保留':''}采集 ${ranking.observed_at?.slice(11,19)||'—'}${quoteFailed?'；部分盘口本轮未更新':''}。— 表示未取得10日涨幅。`
  : `${state.theme} · 日内前排已展示；10日涨幅前5${status&&!status.ok?'获取失败，可手动重试':'待后台补充'}。`;
 $('[data-core-note]').title='区间榜未提供独立行情时间戳，采集时间不代表行情更新时间。'+(ranking?.basis||'');
}

function renderTenDayDetail(){
 const ranking=D.tenDay,s=ranking?.rows.find(r=>r.code===state.stock);
 if(!s){$('[data-detail]').innerHTML='<p class="kt-empty">请选择个股查看数据</p>';return;}
 const q=s.quote;
 $('[data-detail-theme]').textContent=s.theme;
 $('[data-detail]').innerHTML=`<div class="kt-detail-body"><div class="kt-detail-top"><div><span class="kt-detail-name">${esc(s.name)}</span><span class="kt-detail-code">${s.code} · 10日榜第${s.rank}名</span></div><span class="kt-detail-price ${color(q?.change)}">${q?.price?.toFixed(2)||'—'}</span><span class="${color(q?.change)}">${pct(q?.change)}</span></div><div class="kt-detail-stats"><div><span>10日累计涨幅</span><b class="${color(s.return_10d)}">${pct(s.return_10d)}</b></div><div><span>今日成交额</span><b>${money(q?.amount)}</b></div><div><span>今日换手率</span><b>${q?.turnover==null?'—':Number(q.turnover).toFixed(2)+'%'}</b></div><div><span>今日最高 / 最低</span><b>${q?.high?.toFixed(2)||'—'} / ${q?.low?.toFixed(2)||'—'}</b></div></div><p class="kt-reason">${esc(s.theme)}<br>统计区间 ${ranking.start} 至 ${ranking.end}，含首尾10个交易日。全市场榜包含ST，不要求今日涨停。${q?.reason?'<br>'+esc(q.reason):''}</p><div class="kt-detail-source">盘口来源时间 ${esc(q?.source_time||'未取得')} · 区间榜采集 ${esc(ranking.observed_at||'—')}<br>${esc(ranking.basis)}。区间榜未返回独立行情时间戳。</div></div>`;
}
