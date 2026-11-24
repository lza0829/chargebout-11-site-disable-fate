import { useEffect, useState } from 'react'
import { api } from '../api'

export default function Sites() {
  const [list, setList] = useState([])
  const [msg, setMsg] = useState('')
  const [audit, setAudit] = useState(null) // { site, events, gate }
  const load = () => api.站点().then(setList)
  useEffect(() => {
    load()
  }, [])

  const act = async (fn, id, 动作) => {
    setMsg('')
    try {
      const r = await fn(id)
      setMsg(
        `${动作}回执：${r.站点} 闸=${r.闸} 闸序=${r.闸序} 在充会话 ${r.在充会话数} 笔` +
          (r.在充会话数 ? `（${r.在充会话.map((s) => s.会话号).join('、')}，仍可记脉冲与关账收口）` : ''),
      )
      load()
      if (audit?.site?.id === id) showAudit(id) // 审计流水同刷
    } catch (e) {
      setMsg(e.message)
    }
  }

  const showAudit = async (id) => {
    const [events, gate, sites] = await Promise.all([api.站点事件(id), api.站点闸(id), api.站点()])
    setAudit({ site: sites.find((s) => s.id === id) || { id }, events, gate })
  }

  return (
    <div className="page">
      <h2>站点</h2>
      <p className="lead">场站名册与开充闸。下线即禁新开，在充会话照常记脉冲与关账收口。</p>
      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>名称</th>
              <th>地址</th>
              <th>启用</th>
              <th>开充闸</th>
              <th>在充会话</th>
              <th>最近事件</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {list.map((s) => (
              <tr key={s.id}>
                <td>{s.name}</td>
                <td>{s.address}</td>
                <td>{s.active ? '在线' : '已下线'}</td>
                <td>
                  <span className={`tag ${s.gate}`}>{s.gate}</span>
                </td>
                <td>{s.open_sessions}</td>
                <td>{s.last_event_at || '—'}</td>
                <td className="row" style={{ border: 0 }}>
                  {s.active ? (
                    <button type="button" className="danger" onClick={() => act(api.下线, s.id, '下线')}>
                      下线
                    </button>
                  ) : (
                    <button type="button" className="primary" onClick={() => act(api.上线, s.id, '上线')}>
                      上线
                    </button>
                  )}
                  <button type="button" onClick={() => showAudit(s.id)}>
                    流水
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className={`msg ${msg.startsWith('下线回执') || msg.startsWith('上线回执') ? 'ok' : ''}`}>{msg}</p>
      </div>
      {audit && (
        <div className="panel">
          <h3>
            站点审计流水 · {audit.site.name || audit.site.id} · 闸{audit.gate.闸}
            {audit.gate.一致 ? '（物化闸与事件重放一致）' : '（闸态拧花！物化与重放不一致）'}
          </h3>
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>动作</th>
                <th>闸序</th>
                <th>在充会话数</th>
                <th>在充会话</th>
                <th>备注</th>
              </tr>
            </thead>
            <tbody>
              {audit.events.map((e) => (
                <tr key={e.编号}>
                  <td>{e.时间}</td>
                  <td>{e.动作}</td>
                  <td>{e.闸序}</td>
                  <td>{e.在充会话数}</td>
                  <td>{e.在充会话.join('、') || '—'}</td>
                  <td>{e.备注}</td>
                </tr>
              ))}
              {!audit.events.length && (
                <tr>
                  <td colSpan={6}>暂无事件：建站以来未发生下线/上线</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
