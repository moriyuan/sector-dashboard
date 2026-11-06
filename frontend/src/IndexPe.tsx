export type IndexPeRow = {
  code: string; name: string; date: string | null; pe: number | null; percentile: number | null;
  count: number; start: string | null; provider: string; method: string; url: string; backfilled: boolean;
};

export default function IndexPe({rows}: {rows?: IndexPeRow[]}) {
  return <section className="panel" aria-label="主要指数 PE 历史分位">
    <div className="section-head"><div><h3>主要指数 PE 历史分位</h3>
      <p>近 5 年 · 分位越高，表示当前估值高于越多历史交易日。</p></div></div>
    {rows?.length ? <div className="table-wrap"><table><thead><tr>
      <th>指数</th><th>PE（倍）</th><th>历史分位</th><th>估值日</th><th>有效样本</th><th>口径 / 来源</th>
    </tr></thead><tbody>{rows.map(r => <tr key={r.code}>
      <td>{r.name}</td><td>{r.pe?.toFixed(2) ?? '—'}</td>
      <td><strong>{r.percentile == null ? '—' : r.percentile.toFixed(1) + '%'}</strong></td>
      <td>{r.date ?? '待披露'}{r.backfilled && r.date && <small>事后回补</small>}</td>
      <td>{r.start ? `${r.count} 日 · 自 ${r.start}` : '—'}</td>
      <td><a href={r.url} target="_blank" rel="noreferrer">{r.method} · {r.provider}</a></td>
    </tr>)}</tbody></table></div> : <p className="footnote">暂无可读取的指数估值存档。</p>}
    <p className="footnote">每个指数与自身比较，取估值日往前 5 年内严格低于当前 PE 的有效日数占比。仅统计正值，少于 120 日留空。午间仅显示上一交易日或更早的估值。</p>
  </section>;
}
