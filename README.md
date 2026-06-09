# Nserver

Repositório público de distribuição e atualizações do Nserver.

## Instalação inicial

Baixe `releases/0.2.0/nserver-mvp-windows.zip`, extraia em `C:\Nserver` e execute `iniciar-nserver.bat`.

## Atualizações

O aplicativo consulta o manifesto:

```
https://raw.githubusercontent.com/rarodigital/nserver/main/manifest.json
```

As atualizações são aplicadas arquivo a arquivo, com checksum SHA256 e backup automático.

Pastas preservadas sempre:

- `userdata`
- `midias`
- `backups`
- `logs`

## Canais

- `stable`: versões testadas
- `beta`: versões antecipadas
