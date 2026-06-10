# Nserver

Repositório público de distribuição e atualizações do Nserver.

## Instalação inicial

Baixe `releases/0.3.6/nserver-windows-0.3.6.zip`, extraia o conteúdo diretamente em `C:\Nserver` e execute `iniciar-nserver.bat`.

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

## Versão 0.3.7

Inclui a Ferramenta 04 — Editor de Vídeo, com timeline de cortes, preview/final e saída em `midias/Editados`.

## Versão 0.3.8

Padroniza entrada de mídia com URL, Biblioteca e Upload; adiciona Gerenciador de Mídia Central; torna o histórico recolhível; e faz Cortes Virais gerar arquivos reais em `midias/Cortes`.
