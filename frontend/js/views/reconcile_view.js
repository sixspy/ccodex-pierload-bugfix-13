export const reconcileViewTemplate = `
<section class="grid-2">
  <section class="panel">
    <h2>提单对账</h2>
    <p class="hint">汇总过磅毛重与提单申报吨比对；不符即不平衡，影子快照不计入齐套。</p>
    <div class="form-row">
      <div><label>提单号</label><input v-model="reconCode"/></div>
    </div>
    <div class="actions">
      <button @click="loadReconcile">对账</button>
      <button class="secondary" @click="loadAllReconcile">全量</button>
    </div>
    <div v-if="reconOne" class="detail-box">
      <p>申报 {{ reconOne.declared_gross_t }}t · 过磅合计 {{ reconOne.ticket_sum_t }}t</p>
      <p :class="reconOne.balanced ? 'recon-ok' : 'recon-warn'">{{ reconOne.balanced ? '账实平衡' : '毛重不符 · 偏差待查' }}</p>
      <p>正式快照 {{ reconOne.seal_ok_count ?? 0 }} · 影子草稿 {{ reconOne.shadow_seal_count ?? 0 }} · 可出证明 {{ reconOne.proof_ready ? '是' : '否' }}</p>
    </div>
    <ul v-if="reconAllItems.length">
      <li v-for="r in reconAllItems" :key="r.bl_code">
        {{ r.bl_code }} · <span :class="r.balanced ? 'recon-ok' : 'recon-warn'">{{ r.balanced ? '平' : '偏' }}</span>
      </li>
    </ul>
  </section>
  <section class="panel">
    <h2>单票证明</h2>
    <div class="form-row"><div><label>票号</label><input v-model="proofCode"/></div></div>
    <div class="actions"><button @click="loadProof">拉证明</button></div>
    <div v-if="proofOne" class="detail-box">
      <p>提单匹配 {{ proofOne.manifest_matches_bl ? '是' : '否' }}</p>
      <p>快照性质 <span :class="proofOne.seal_formal ? 'recon-ok' : 'recon-warn'">{{ proofOne.seal_formal ? '正式闸代' : (proofOne.has_seal ? '影子草稿' : '无快照') }}</span></p>
      <p>超重闭环 {{ proofOne.overload_closed ? '是' : '否' }}</p>
      <p>已放行 {{ proofOne.released ? '是' : '否' }}</p>
      <p>一致 {{ proofOne.coherent ? '是' : '否' }}</p>
    </div>
  </section>
</section>
`;
