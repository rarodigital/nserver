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

## Versão 0.3.9

Reimplementa a Ferramenta 04 como CutFlow Studio: corte de silêncios, `cuts.json`, preview protegido, render final e legenda dentro do estilo Nserver.

## Versão 0.3.10

Corrige entradas da Ferramenta 04: Biblioteca/Upload/URL visíveis, aba Legenda independente, rota `/library` e streaming de arquivos restaurados.

## Versão 0.3.11

Hotfix: restaura Atualizações, melhora URL/upload no CutFlow e corrige duração dos Cortes Virais quando `ffprobe` não está disponível.

## Versão 0.3.12

Corrige o JavaScript da Ferramenta 04, simplifica a Etapa 1 para uma única origem selecionável e finaliza o fallback de duração dos Cortes Virais.

## Versão 0.3.13

Hotfix focado: carregamento de vídeo da Biblioteca na Ferramenta 04 agora não depende de `ffprobe` estar instalado no PATH do Windows.

## Versão 0.3.14

Hotfix do editor: vídeos TikTok em HEVC/H.265 ganham proxy H.264 automático para preview no navegador, sem trocar o arquivo original do projeto.

## Versão 0.3.15

Melhora a experiência do editor com HEVC/TikTok: mostra carregamento enquanto gera preview H.264 e só carrega o player depois do `play_url` estar pronto.

## Versão 0.3.16

URL na Ferramenta 04 agora vira fonte temporária do editor, sem salvar/duplicar o vídeo original na Biblioteca. O resultado final continua sendo exportado para a Biblioteca.
