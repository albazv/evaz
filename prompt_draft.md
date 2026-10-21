# Teamwork Project Prompt — Draft

> Status: Launched
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: [none — teamwork routes from the description]

Refatoração completa do site institucional da marca para a nova identidade visual e posicionamento COMELVA (anteriormente EVAZ), implementando um portal e-commerce standalone de alta conversão com Tailwind CSS, catálogo de produtos tech, design system oficial (amarelo solar #FFE600 / preto #111111 / DM Sans) e SEO técnico.

Working directory: /Users/macazv/Library/Mobile Documents/com~apple~CloudDocs/Evaz/06_Site_Sistemas_e_Automacoes/site-institucional
Integrity mode: development

## Reference Assets
- Brand Kit Comelva: `/Users/macazv/Documents/Comelva` (Logos 4K/SVG em `01_Logos`, Símbolos/Favicons em `02_Simbolos_e_Icones`, Tipografia em `03_Tipografia`, Paleta de Cores em `04_Paleta_de_Cores`).
- Dados da Empresa MEI: Razão Social `69.355.087 HANA FERREIRA PONTE`, CNPJ `69.355.087/0001-16`, Brasília - DF.

## Requirements

### R1. Rebranding Visual 360° com Design System Comelva
Substituir integralmente a antiga identidade EVAZ pela nova marca COMELVA em todas as páginas (`index.html`, `termos-de-uso.html`, `politica-de-privacidade.html`):
- Copiar e integrar os logotipos oficiais (SVGs vetoriais e PNGs 4K) da pasta `/Users/macazv/Documents/Comelva/01_Logos` para o diretório `assets/`.
- Aplicar a paleta oficial: Amarelo Solar (`#FFE600`), Preto Obsidian (`#111111`), Grafite Sideral (`#303238`), Branco Alabaster (`#F5F5F7` / `#FFFFFF`).
- Configurar a tipografia DM Sans (via Google Fonts e fallbacks sans-serif) com hierarquia balanceada.
- Integrar o kit completo de Favicon multi-resolução (`favicon.ico`, `apple-touch-icon`, `site.webmanifest`).

### R2. Arquitetura de Conversão, Catálogo Tech & Interatividade
Construir uma experiência de compra moderna, limpa e focada em conversão:
- **Header:** Logotipo Comelva, navegação para categorias (Hardware PRO, Armazenamento, Cabos & Conexões, Linha Originals), badge de frete grátis e botão de carrinho com contador de itens.
- **Hero Section:** Headline de alto impacto ("Precisão para o seu setup, potência para o seu espaço"), visual do produto flagship e CTAs primários ("Comprar Agora" e "Explorar Catálogo").
- **Bento Grid de Produtos:** Cards interativos para a linha de produtos (Logitech G PRO X2 Superstrike, G PRO X TKL, MK250, Echo Dot Max, Kits de Organização e Cabos 240W), com fotos de alta qualidade, especificações técnicas resumidas, preço à vista com desconto Pix e botão de compra.
- **Carrinho Drawer & Checkout:** Drawer lateral funcional com lista de itens, seletor de quantidade, cálculo de frete simulado e finalização via WhatsApp ou link de pagamento.
- **Autoridade & Confiança:** Selo de entrega expressa de Brasília, garantia oficial, política de 7 dias de devolução incondicional e rodapé institucional com dados cadastrais completos (Razão Social, CNPJ, endereço e contato).

### R3. SEO Técnico, Metatags OpenGraph e Dados Estruturados
Garantir indexação de excelência e pré-visualizações perfeitas nas redes sociais:
- Títulos, descrições e palavras-chave 100% atualizados para COMELVA.
- Tags OpenGraph (`og:title`, `og:description`, `og:image`, `og:site_name`) e Twitter Cards apontando para o logo oficial e banner 1200x630px da Comelva.
- Dados estruturados JSON-LD Schema.org válidos com tipos `OnlineStore`, `Brand`, `Product` e `PostalAddress`.
- Atualizar arquivos auxiliares `sitemap.xml`, `robots.txt` e páginas legais.

### R4. Acessibilidade (WCAG AA) e Performance Extrema
- Garantir conformidade estrita de contraste: texto escuro (`#111111`) sobre fundos Amarelo Solar (`#FFE600`), nunca texto branco.
- Zero layout shift (CLS = 0) com dimensões explícitas em imagens e carregamento otimizado.
- Código limpo, semântico e autossuficiente (HTML5 + Tailwind CSS standalone), compatível com qualquer hospedagem estática.

## Acceptance Criteria

### Identidade e Ativos
- [ ] Não há referências visuais residuais da marca antiga EVAZ em logotipos, favicons, slogans ou cabeçalhos.
- [ ] Todos os caminhos de imagens em `assets/` resolvem para arquivos existentes com status HTTP 200.
- [ ] O Favicon oficial da Comelva é exibido na aba do navegador.

### Funcionalidade e Interatividade
- [ ] O drawer do carrinho de compras abre, fecha, atualiza a quantidade de produtos e calcula o total dinamicamente.
- [ ] Os botões de ação ("Comprar", "Adicionar ao Carrinho", "Chamar no WhatsApp") disparam os fluxos corretos sem erros no console.
- [ ] O layout adapta-se fluidamente sem quebras tanto em tela desktop (1440px) quanto em dispositivos móveis (375px/390px).

### Dados e Conformidade
- [ ] O rodapé exibe com clareza: `69.355.087 HANA FERREIRA PONTE | CNPJ: 69.355.087/0001-16 | Brasília - DF`.
- [ ] O validador de Schema.org JSON-LD passa sem erros de sintaxe.
- [ ] Nenhuma violação crítica de contraste WCAG AA entre texto e fundo.

---
*Next: when approved → delegate via invoke_subagent (see Delegation Protocol)*
