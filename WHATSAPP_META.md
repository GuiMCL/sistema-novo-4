# WhatsApp Cloud API (Meta)

Esta aplicação usa exclusivamente a API oficial da Meta. Evolution API, Redis e
PostgreSQL dela não fazem mais parte do `docker-compose.yml`.

## Configurar

1. No [Meta for Developers](https://developers.facebook.com/apps/), crie ou selecione um app do tipo **Business** e adicione o produto **WhatsApp**.
2. No WhatsApp Manager, crie/conecte a conta WhatsApp Business, adicione e verifique o número que atenderá clientes. O número não é pareado por QR Code.
3. Em **API Setup**, copie o **Phone number ID** e gere um token de acesso. Para produção, use um token de usuário do sistema com permissões `whatsapp_business_messaging` e `whatsapp_business_management`; não use o token temporário.
4. Em **Settings > Basic** do app, copie o **App Secret**. Escolha um texto aleatório longo para o token de verificação do webhook.
5. Preencha no `.env`:

   ```env
   WHATSAPP_ACCESS_TOKEN=token-permanente-da-meta
   WHATSAPP_PHONE_NUMBER_ID=123456789012345
   WHATSAPP_VERIFY_TOKEN=um-segredo-aleatorio-so-seu
   WHATSAPP_APP_SECRET=app-secret-da-meta
   ```

6. Publique esta aplicação em uma URL HTTPS acessível pela internet. Para desenvolvimento local, exponha a porta 8000 por um túnel HTTPS. Cadastre no painel Meta a URL `https://SEU-DOMINIO/webhook/whatsapp`, informe o mesmo `WHATSAPP_VERIFY_TOKEN` e assine o campo **messages**.
7. Execute `docker compose up -d --build`. O painel permanece em `http://localhost:8000/admin`.

## Testar

1. Abra o painel: o card “WhatsApp Business (Meta)” deve mostrar o nome/número aprovado.
2. Na tela **API Setup** da Meta, envie uma mensagem de teste para um telefone permitido (em modo desenvolvimento, adicione-o à lista de destinatários de teste).
3. Envie uma mensagem desse telefone para o número Business. A Meta deve receber HTTP 200 no webhook e a conversa deve aparecer no painel.
4. Responda pela conversa do painel ou pelo bot. Dentro da janela de atendimento de 24 horas, a mensagem de texto é enviada normalmente. Fora dela, use um template aprovado pela Meta para mensagens iniciadas pelo negócio.

O endpoint rejeita webhooks sem `X-Hub-Signature-256` válido; mantenha o `WHATSAPP_APP_SECRET` preenchido em todos os ambientes.

## Templates de lembrete de agendamento

No **WhatsApp Manager**, abra **Account tools > Message templates**, crie dois
templates da categoria **Utility** em `Português (BR)` e aguarde a aprovação.
Use nomes minúsculos, sem espaços, exatamente como os configurados no `.env`:

| Nome | Corpo do template |
| --- | --- |
| `lembrete_agendamento` | `Olá {{1}}, lembramos do seu agendamento de {{2}} em {{3}}. Caso precise alterar, responda esta mensagem.` |
| `confirmacao_agendamento` | `Olá {{1}}, seu agendamento de {{2}} é em {{3}}. Pode confirmar sua presença respondendo a esta mensagem?` |

As três variáveis são enviadas pela aplicação nesta ordem: nome do cliente,
serviço e data. Não altere essa quantidade ou ordem sem alterar o código.

Após a aprovação, confirme no `.env`:

```env
WHATSAPP_APPOINTMENT_TEMPLATE=lembrete_agendamento
WHATSAPP_APPOINTMENT_TEMPLATE_2=confirmacao_agendamento
WHATSAPP_TEMPLATE_LANGUAGE=pt_BR
```

Reinicie com `docker compose up -d --build`. O primeiro aviso e o segundo
passam a usar template automaticamente; se os nomes ficarem vazios, o sistema
mantém o envio de texto livre, que a Meta aceita apenas durante a janela de 24h.
