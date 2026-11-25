export const sealViewTemplate = `
<section class="panel">
  <h2>闸代快照</h2>
  <p class="hint">封闸升代并写入清单摘要。闸代读数须与提单申报毛重相符方可封闸；不符当场回滚，快照行零增。</p>
  <div class="form-row">
    <div><label>过磅票</label>
      <select v-model.number="sealForm.ticket_id">
        <option v-for="t in sealableTickets" :key="t.id" :value="t.id">
          {{ t.ticket_code }} · {{ t.gross_match ? '毛重相符' : '毛重不符' }}
        </option>
      </select>
    </div>
    <div><label>清单(可空)</label><input v-model="sealForm.manifest"/></div>
  </div>
  <div class="actions">
    <button @click="createSeal">写入快照</button>
    <button class="secondary" @click="refresh">刷新</button>
  </div>
  <p v-if="!sealableTickets.length" class="hint bad">
    当前无毛重相符且可封闸的票；毛重不符者一律禁封，须先处置差异。
  </p>
  <table class="data-table">
    <thead><tr><th>快照号</th><th>票号</th><th>闸代</th><th>申报吨</th><th>闸代读数</th><th>核销</th><th>清单摘要</th><th>状态</th></tr></thead>
    <tbody>
      <tr v-for="s in seals" :key="s.id">
        <td>{{ s.seal_ref }}</td>
        <td>{{ s.ticket_code }}</td>
        <td>{{ s.ticket_generation }}</td>
        <td>{{ fmtT(s.declared_gross_t) }}</td>
        <td>{{ fmtT(s.manifest_gross_t) }}</td>
        <td>
          <span class="badge" :class="s.gross_match ? 'pass' : 'fail'">
            {{ s.gross_match ? '正式闸代' : '影子草稿' }}
          </span>
        </td>
        <td class="mono">{{ shortText(s.manifest) }}</td>
        <td><span class="badge pass">{{ s.status }}</span></td>
      </tr>
    </tbody>
  </table>
</section>
`;
