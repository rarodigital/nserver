# Nserver

Repositório público de distribuição e atualizações do Nserver.

## Instalação inicial

Baixe `releases/0.3.2/nserver-windows-0.3.2.zip`, extraia o conteúdo diretamente em `C:\Nserver` e execute `iniciar-nserver.bat`.

## Manifesto de atualização

```
https://raw.githubusercontent.com/rarodigital/nserver/main/manifest.json
```

O Nserver verifica esse manifesto ao abrir pelo launcher e também pela página **Atualizações**.

As atualizações são aplicadas arquivo a arquivo, com checksum SHA256 e backup automático.

Pastas preservadas sempre:

- `userdata`
- `midias`
- `backups`
- `logs`
