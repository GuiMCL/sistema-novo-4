const fs = require('fs');
const path = require('path');

const source = process.argv[2];
const target = process.argv[3];
if (!source || !target) throw new Error('Uso: node scripts/patch_n8n_ads.cjs <origem.json> <destino.json>');
const workflow = JSON.parse(fs.readFileSync(source, 'utf8'));
const ADS_SECRET = 'revize-ads-2026-segredo';
const CALLBACK_URL = 'https://api.revizeautocenter.shop/admin/ads/callback';
const node = name => workflow.nodes.find(n => n.name === name);

const config = node('CONFIG - Meta');
config.parameters.jsCode = config.parameters.jsCode.replace(
  "ads_webhook_secret:'COLE_SEU_SEGREDO_ADS_AQUI'",
  `ads_webhook_secret:'${ADS_SECRET}'`
);

const validar = node('Validar briefing');
validar.parameters.jsCode = validar.parameters.jsCode.replace(
  "const obj={id,nome:title",
  "const obj={client_request_id:String(b.client_request_id||''),id,nome:title"
);

function callback(name, etapa, sourceNode) {
  return {
    parameters: {
      method: 'POST',
      url: `=${CALLBACK_URL}`,
      sendHeaders: true,
      headerParameters: { parameters: [{ name: 'X-Ads-Callback-Secret', value: ADS_SECRET }] },
      sendBody: true,
      specifyBody: 'json',
      jsonBody: `={{ JSON.stringify({client_request_id:String($("Validar briefing").first().json.client_request_id||''),etapa:'${etapa}',status:'processando',copy:$("Interpretar criativos").first().json.copy||{},image_b64:String($("Consolidar imagem").first().json.image_b64||''),meta:{n8n_execution_id:String($execution.id),source:'${sourceNode}'}}) }}`,
      options: {}
    },
    id: `callback-${etapa}-${Date.now()}`,
    name,
    type: 'n8n-nodes-base.httpRequest',
    typeVersion: 4.2,
    position: [1200, 220 + (etapa === 'rascunho_salvo' ? 180 : 0)]
  };
}

const imagem = callback('CALLBACK - Imagem gerada', 'imagem_gerada', 'Consolidar imagem');
const salvo = callback('CALLBACK - Rascunho salvo', 'rascunho_salvo', 'Salvar rascunho');
workflow.nodes.push(imagem, salvo);

function replaceNext(from, oldNext, newNext) {
  const con = workflow.connections[from];
  if (!con?.main?.[0]) return;
  con.main[0] = con.main[0].map(item => item.node === oldNext ? { ...item, node: newNext } : item);
}
replaceNext('Consolidar imagem', 'Salvar rascunho', 'CALLBACK - Imagem gerada');
workflow.connections['CALLBACK - Imagem gerada'] = { main: [[{ node: 'Salvar rascunho', type: 'main', index: 0 }]] };
workflow.connections['Salvar rascunho'] = workflow.connections['Salvar rascunho'] || { main: [[]] };
workflow.connections['Salvar rascunho'].main[0] = [{ node: 'CALLBACK - Rascunho salvo', type: 'main', index: 0 }];
workflow.connections['CALLBACK - Rascunho salvo'] = { main: [[]] };

fs.mkdirSync(path.dirname(target), { recursive: true });
let output = JSON.stringify(workflow, null, 2);
output = output.replace(/\$env\.ADS_WEBHOOK_SECRET/g, `'${ADS_SECRET}'`);
output = output.replace(/\$env\.WAIA_WEBHOOK_SECRET/g, `'${ADS_SECRET}'`);
fs.writeFileSync(target, output + '\n');
console.log(target);
