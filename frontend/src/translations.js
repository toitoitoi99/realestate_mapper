// Vocabulary lookup for Portuguese terms that come from the database.
// Used by translateTerm() — terms not in the map are returned unchanged.
export const PT_TERMS = {
  // OP_URBANISTICA
  'Alteração':                               'Alteration',
  'Alteração Durante a Execução da Obra':    'Alteration During Construction',
  'Ampliação':                               'Extension',
  'Construção':                              'Construction',
  'Construção Nova':                         'New Construction',
  'Demolição':                               'Demolition',
  'Informação Prévia':                       'Prior Information Request',
  'Obras de Conservação':                    'Conservation Works',
  'Reconstrução':                            'Reconstruction',
  'Renovação de Licença (artigo 72º do RJUE)': 'Licence Renewal (Art. 72)',
  'Não Identificada':                        'Unidentified',

  // ASSUNTO
  'Alterações Exteriores':                   'Exterior Alterations',
  'Alterações Interiores':                   'Interior Alterations',
  'Alteração ao projecto de arquitectura':   'Architectural Design Change',
  'Alteração a projecto de especialidade':   'Specialist Project Change',
  'Alterações Sujeitas a Licença':           'Licensed Alterations',
  'Ao abrigo do nº 2 do Art.º 14':          'Under Art. 14(2)',
  'Não está ao abrigo do nº 2 do Art.º 14': 'Not Under Art. 14(2)',

  // TIPOLOGIA
  'Edificação':                              'Building',
  'Loteamento':                              'Land Division',
  'Utilização':                              'Change of Use',

  // PROCEDIMENTO
  'Licença':                                 'Licence',
  'Comunicação Prévia':                      'Prior Notice',
  'Autorização':                             'Authorization',
  'Informação Prévia':                       'Prior Information',

  // Shared
  'Sem Informação':                          'No Information',
}

export const translations = {
  en: {
    // StatsBar
    appTitle:        'Lisbon Real Estate',
    listings:        'listings',
    avgAsk:          'avg ask',
    perSqmAsk:       '/m² ask',
    perSqmSold:      '/m² sold',
    neighborhoods:   'neighborhoods',
    lastScrape:      'Last scrape:',
    runScraper:      'Run scraper',
    scraping:        'Scraping…',

    // FilterPanel
    filters:         'Filters',
    reset:           'Reset',
    listingType:     'Listing type',
    buy:             'Buy',
    rent:            'Rent',
    price:           'Price (€)',
    monthlyRent:     'Monthly rent (€)',
    size:            'Size (m²)',
    rooms:           'Rooms (typology)',
    any:             'Any',
    showSold:        'Show sold / reserved',
    soldDateRange:   'Sold / reserved date range',
    sortBy:          'Sort by',
    sortDefault:     'Most recent',
    sortRarity:      'Rarity (unique first)',
    sortPriceAsc:    'Price (low → high)',
    sortPriceDesc:   'Price (high → low)',
    listingsCount:   (n) => `${n} listings`,

    // Sidebar
    loading:         'Loading…',
    noListings:      'No listings found',

    // ListingCard
    inArea:          (neighborhood) => `in ${neighborhood}`,

    // Map popups
    viewListing:     'View listing →',
    sold:            'Sold',
    reserved:        'Reserved',

    // MapLegend
    mapLayers:       'Map layers',
    forSale:         'For sale',
    rentLabel:       'Rent',
    soldLabel:       'Sold',
    constructionProjects: 'Construction projects',
    issuedPermit:    'Issued permit',
    pending:         'Pending',
    security:        'Security',
    pspStations:     'PSP stations',
    municipalPolice: 'Municipal police',
    cctvCameras:     'CCTV cameras (Bairro Alto)',

    // Neighborhood groups
    neighborhoods:   'Neighborhoods',
    historic:        'Historic & Central',
    riverside:       'Riverside & Western',
    uptown:          'Uptown & Modern',
    residential:     'Inner Residential',
    outer:           'Outer Districts',
    askingAvg:       'Asking avg',
    soldAvg:         'Sold avg',

    // ProjectLayer popup
    submitted:       'Submitted:',
    permit:          'Permit:',
    issued:          'Issued:',

    // SecurityLayer popup
    pspStation:      'PSP station',
    municipalPoliceLabel: 'Municipal police',
    cctvCamera:      'CCTV camera',
  },

  pt: {
    // StatsBar
    appTitle:        'Imobiliário de Lisboa',
    listings:        'anúncios',
    avgAsk:          'preço médio',
    perSqmAsk:       '/m² pedido',
    perSqmSold:      '/m² vendido',
    neighborhoods:   'bairros',
    lastScrape:      'Última actualização:',
    runScraper:      'Actualizar dados',
    scraping:        'A actualizar…',

    // FilterPanel
    filters:         'Filtros',
    reset:           'Limpar',
    listingType:     'Tipo de anúncio',
    buy:             'Compra',
    rent:            'Arrendamento',
    price:           'Preço (€)',
    monthlyRent:     'Renda mensal (€)',
    size:            'Área (m²)',
    rooms:           'Tipologia',
    any:             'Qualquer',
    showSold:        'Mostrar vendidos / reservados',
    soldDateRange:   'Intervalo de datas (vendido/reservado)',
    sortBy:          'Ordenar por',
    sortDefault:     'Mais recente',
    sortRarity:      'Raridade (único primeiro)',
    sortPriceAsc:    'Preço (menor → maior)',
    sortPriceDesc:   'Preço (maior → menor)',
    listingsCount:   (n) => `${n} anúncios`,

    // Sidebar
    loading:         'A carregar…',
    noListings:      'Sem anúncios encontrados',

    // ListingCard
    inArea:          (neighborhood) => `em ${neighborhood}`,

    // Map popups
    viewListing:     'Ver anúncio →',
    sold:            'Vendido',
    reserved:        'Reservado',

    // MapLegend
    mapLayers:       'Camadas do mapa',
    forSale:         'Venda',
    rentLabel:       'Arrendamento',
    soldLabel:       'Vendido',
    constructionProjects: 'Projectos de construção',
    issuedPermit:    'Alvará emitido',
    pending:         'Em curso',
    security:        'Segurança',
    pspStations:     'Esquadras PSP',
    municipalPolice: 'Polícia Municipal',
    cctvCameras:     'Câmaras CCTV (Bairro Alto)',

    // Neighborhood groups
    neighborhoods:   'Bairros',
    historic:        'Histórico & Central',
    riverside:       'Ribeirinha & Ocidental',
    uptown:          'Uptown & Moderno',
    residential:     'Residencial Interior',
    outer:           'Periferia',
    askingAvg:       'Pedido médio',
    soldAvg:         'Vendido médio',

    // ProjectLayer popup
    submitted:       'Entrada:',
    permit:          'Alvará:',
    issued:          'Emitido:',

    // SecurityLayer popup
    pspStation:      'Esquadra PSP',
    municipalPoliceLabel: 'Polícia Municipal',
    cctvCamera:      'Câmara CCTV',
  },
}
