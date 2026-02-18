// Главный менеджер для работы с грузами
import { generateMockLoads } from './mocks.js';
import { sortLoads, filterLoads, validateFilters, getActiveFilters, calculateRiskLevel } from './utils.js';
import { SORT_ORDERS, COLUMNS, RISK_COLORS, ITEMS_PER_PAGE_OPTIONS } from './constants.js';
import { saveToLocalStorage, loadFromLocalStorage, formatPrice, formatDate, formatTime, formatDistance } from './utils.js';

class CargoManager {
    constructor() {
        this.allLoads = [];
        this.filteredLoads = [];
        this.displayedLoads = [];
        this.filters = {
            from: '',
            to: '',
            date: '',
            weightFrom: '',
            weightTo: '',
            volumeFrom: '',
            volumeTo: '',
            truckType: ''
        };
        this.sortState = {
            column: null,
            order: SORT_ORDERS.NONE
        };
        this.visibleColumns = loadFromLocalStorage('cargo_visible_columns', {
            [COLUMNS.CARGO_WEIGHT]: true,
            [COLUMNS.ROUTE]: true,
            [COLUMNS.PRICE]: true,
            [COLUMNS.DATE]: true,
            [COLUMNS.TRUCK_TYPE]: true,
            [COLUMNS.DISTANCE]: true,
            [COLUMNS.RISK]: true,
            [COLUMNS.ACTIONS]: true
        });
        this.currentPage = 1;
        this.itemsPerPage = loadFromLocalStorage('cargo_items_per_page', 20);
        this.filtersCollapsed = loadFromLocalStorage('cargo_filters_collapsed', false);
        
        this.loadState();
    }
    
    init() {
        // Загружаем моки или реальные данные
        this.allLoads = generateMockLoads(80);
        this.applyFiltersAndSort();
        this.render();
    }
    
    loadState() {
        const savedFilters = loadFromLocalStorage('cargo_filters', null);
        if (savedFilters) {
            this.filters = { ...this.filters, ...savedFilters };
        }
        
        const savedSort = loadFromLocalStorage('cargo_sort', null);
        if (savedSort) {
            this.sortState = savedSort;
        }
    }
    
    saveState() {
        saveToLocalStorage('cargo_filters', this.filters);
        saveToLocalStorage('cargo_sort', this.sortState);
        saveToLocalStorage('cargo_visible_columns', this.visibleColumns);
        saveToLocalStorage('cargo_items_per_page', this.itemsPerPage);
        saveToLocalStorage('cargo_filters_collapsed', this.filtersCollapsed);
    }
    
    setFilter(key, value) {
        this.filters[key] = value;
        this.currentPage = 1;
        this.applyFiltersAndSort();
        this.saveState();
        this.render();
    }
    
    clearFilters() {
        this.filters = {
            from: '',
            to: '',
            date: '',
            weightFrom: '',
            weightTo: '',
            volumeFrom: '',
            volumeTo: '',
            truckType: ''
        };
        this.currentPage = 1;
        this.applyFiltersAndSort();
        this.saveState();
        this.render();
    }
    
    setSort(column) {
        if (this.sortState.column === column) {
            // Циклическая смена: asc -> desc -> none
            if (this.sortState.order === SORT_ORDERS.ASC) {
                this.sortState.order = SORT_ORDERS.DESC;
            } else if (this.sortState.order === SORT_ORDERS.DESC) {
                this.sortState.order = SORT_ORDERS.NONE;
                this.sortState.column = null;
            }
        } else {
            this.sortState.column = column;
            this.sortState.order = SORT_ORDERS.ASC;
        }
        this.applyFiltersAndSort();
        this.saveState();
        this.render();
    }
    
    toggleColumn(column) {
        this.visibleColumns[column] = !this.visibleColumns[column];
        this.saveState();
        this.render();
    }
    
    setItemsPerPage(count) {
        this.itemsPerPage = count;
        this.currentPage = 1;
        this.saveState();
        this.render();
    }
    
    setPage(page) {
        this.currentPage = page;
        this.render();
    }
    
    toggleFiltersCollapse() {
        this.filtersCollapsed = !this.filtersCollapsed;
        this.saveState();
        this.render();
    }
    
    applyFiltersAndSort() {
        // Валидация фильтров
        const errors = validateFilters(this.filters);
        if (Object.keys(errors).length > 0) {
            console.warn('Filter validation errors:', errors);
        }
        
        // Фильтрация
        this.filteredLoads = filterLoads(this.allLoads, this.filters);
        
        // Расчет рисков
        this.filteredLoads.forEach(load => {
            if (!load.risk_level) {
                const risk = calculateRiskLevel(load, this.allLoads);
                load.risk_level = risk.level;
                load.risk_score = risk.score;
                load.risk_reasons = risk.reasons;
            }
        });
        
        // Сортировка
        if (this.sortState.column && this.sortState.order !== SORT_ORDERS.NONE) {
            this.filteredLoads = sortLoads(this.filteredLoads, this.sortState.column, this.sortState.order);
        }
        
        // Пагинация
        const start = (this.currentPage - 1) * this.itemsPerPage;
        const end = start + this.itemsPerPage;
        this.displayedLoads = this.filteredLoads.slice(start, end);
    }
    
    addLoad(loadData) {
        const newLoad = {
            id: this.allLoads.length + 1,
            ...loadData,
            created_at: new Date().toISOString(),
            status: 'open',
            price_per_km: loadData.distance ? Math.round(loadData.price / loadData.distance) : 0
        };
        
        // Расчет риска для нового груза
        const risk = calculateRiskLevel(newLoad, this.allLoads);
        newLoad.risk_level = risk.level;
        newLoad.risk_score = risk.score;
        newLoad.risk_reasons = risk.reasons;
        
        this.allLoads.unshift(newLoad);
        this.applyFiltersAndSort();
        this.render();
    }
    
    render() {
        this.renderFilters();
        this.renderActiveFilters();
        this.renderTable();
        this.renderPagination();
    }
    
    renderFilters() {
        // Обновление значений фильтров в DOM
        document.getElementById('filter-from').value = this.filters.from;
        document.getElementById('filter-to').value = this.filters.to;
        document.getElementById('filter-date').value = this.filters.date;
        document.getElementById('filter-weight-from').value = this.filters.weightFrom;
        document.getElementById('filter-weight-to').value = this.filters.weightTo;
        document.getElementById('filter-volume-from').value = this.filters.volumeFrom;
        document.getElementById('filter-volume-to').value = this.filters.volumeTo;
        document.getElementById('filter-truck-type').value = this.filters.truckType;
        
        // Сворачивание панели
        const filtersPanel = document.getElementById('filtersPanel');
        if (this.filtersCollapsed) {
            filtersPanel.classList.add('hidden');
        } else {
            filtersPanel.classList.remove('hidden');
        }
    }
    
    renderActiveFilters() {
        const activeFilters = getActiveFilters(this.filters);
        const container = document.getElementById('activeFiltersContainer');
        
        if (activeFilters.length === 0) {
            container.innerHTML = '';
            return;
        }
        
        container.innerHTML = activeFilters.map(filter => `
            <div class="inline-flex items-center gap-2 px-3 py-1 bg-purple/20 border border-purple/30 rounded-full text-sm">
                <span>${filter.label}</span>
                <button onclick="cargoManager.removeFilter('${filter.key}')" class="hover:text-red-400 transition-colors">
                    ✕
                </button>
            </div>
        `).join('');
    }
    
    removeFilter(key) {
        if (key === 'weight') {
            this.filters.weightFrom = '';
            this.filters.weightTo = '';
        } else if (key === 'volume') {
            this.filters.volumeFrom = '';
            this.filters.volumeTo = '';
        } else {
            this.filters[key] = '';
        }
        this.currentPage = 1;
        this.applyFiltersAndSort();
        this.saveState();
        this.render();
    }
    
    renderTable() {
        const tbody = document.getElementById('loadsTableBody');
        
        if (this.displayedLoads.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" class="px-4 py-12 text-center">
                        <div class="flex flex-col items-center gap-4">
                            <div class="text-6xl">📦</div>
                            <div class="text-xl font-semibold">Грузов не найдено</div>
                            <div class="text-gray-400 text-sm">Попробуйте изменить фильтры или</div>
                            <button onclick="openModal('createLoad')" class="px-6 py-2 bg-gradient-to-r from-purple to-blue hover:from-purple-dark hover:to-blue-dark rounded-lg font-semibold transition-all">
                                ➕ Добавить груз
                            </button>
                        </div>
                    </td>
                </tr>
            `;
            return;
        }
        
        tbody.innerHTML = this.displayedLoads.map(load => {
            const riskBadge = this.getRiskBadge(load);
            const sortIcon = this.getSortIcon('price');
            
            return `
                <tr class="hover:bg-dark-border/50 transition-colors cursor-pointer border-b border-dark-border/50" onclick="showLoadCard(${load.id})">
                    ${this.visibleColumns[COLUMNS.CARGO_WEIGHT] ? `
                    <td class="px-4 py-3">
                        <div class="font-semibold">${load.weight || '—'} т / ${load.volume || '—'} м³</div>
                        <div class="text-xs text-gray-400">ID: #${load.id}</div>
                    </td>
                    ` : ''}
                    ${this.visibleColumns[COLUMNS.ROUTE] ? `
                    <td class="px-4 py-3">
                        <div class="flex items-center gap-2">
                            <span class="text-blue">${load.from_city}</span>
                            <span>→</span>
                            <span class="text-purple">${load.to_city}</span>
                        </div>
                        ${load.from_address || load.to_address ? `
                        <div class="text-xs text-gray-500 mt-1">
                            ${load.from_address || ''} ${load.to_address ? '→ ' + load.to_address : ''}
                        </div>
                        ` : ''}
                    </td>
                    ` : ''}
                    ${this.visibleColumns[COLUMNS.PRICE] ? `
                    <td class="px-4 py-3">
                        <div class="font-semibold text-green-400">${formatPrice(load.price)}</div>
                        <div class="text-xs text-gray-400">${load.price_per_km || 0} ₽/км</div>
                    </td>
                    ` : ''}
                    ${this.visibleColumns[COLUMNS.DATE] ? `
                    <td class="px-4 py-3">
                        <div class="text-sm">${formatDate(load.created_at)}</div>
                        <div class="text-xs text-gray-400">${formatTime(load.created_at)}</div>
                        ${load.loading_date ? `
                        <div class="text-xs text-purple mt-1">Погрузка: ${formatDate(load.loading_date)}</div>
                        ` : ''}
                    </td>
                    ` : ''}
                    ${this.visibleColumns[COLUMNS.TRUCK_TYPE] ? `
                    <td class="px-4 py-3">
                        <span class="text-sm">${load.truck_type || '—'}</span>
                    </td>
                    ` : ''}
                    ${this.visibleColumns[COLUMNS.DISTANCE] ? `
                    <td class="px-4 py-3">
                        <span class="text-sm">${formatDistance(load.distance)}</span>
                    </td>
                    ` : ''}
                    ${this.visibleColumns[COLUMNS.RISK] ? `
                    <td class="px-4 py-3">
                        ${riskBadge}
                    </td>
                    ` : ''}
                    ${this.visibleColumns[COLUMNS.ACTIONS] ? `
                    <td class="px-4 py-3" onclick="event.stopPropagation()">
                        <button onclick="addBid(${load.id})" class="px-3 py-1 bg-gradient-to-r from-purple to-blue hover:from-purple-dark hover:to-blue-dark rounded text-xs font-semibold transition-all">
                            Взять
                        </button>
                    </td>
                    ` : ''}
                </tr>
            `;
        }).join('');
    }
    
    getRiskBadge(load) {
        const level = load.risk_level || 'low';
        const score = load.risk_score || 0;
        const colors = RISK_COLORS[level] || RISK_COLORS.low;
        const labels = {
            low: 'Низкий',
            medium: 'Средний',
            high: 'Высокий'
        };
        
        return `
            <span class="px-2 py-1 rounded text-xs ${colors}" title="${load.risk_reasons ? load.risk_reasons.join(', ') : ''}">
                ${labels[level]} (${score}%)
            </span>
        `;
    }
    
    getSortIcon(column) {
        if (this.sortState.column !== column) {
            return '<span class="text-gray-500">⇅</span>';
        }
        if (this.sortState.order === SORT_ORDERS.ASC) {
            return '<span class="text-purple">↑</span>';
        }
        if (this.sortState.order === SORT_ORDERS.DESC) {
            return '<span class="text-purple">↓</span>';
        }
        return '<span class="text-gray-500">⇅</span>';
    }
    
    renderPagination() {
        const totalPages = Math.ceil(this.filteredLoads.length / this.itemsPerPage);
        const container = document.getElementById('paginationContainer');
        
        if (totalPages <= 1) {
            container.innerHTML = '';
            return;
        }
        
        let paginationHTML = `
            <div class="flex items-center justify-between px-4 py-3 border-t border-dark-border">
                <div class="flex items-center gap-2">
                    <span class="text-sm text-gray-400">Показано:</span>
                    <select onchange="cargoManager.setItemsPerPage(parseInt(this.value))" class="px-2 py-1 bg-dark border border-dark-border rounded text-sm">
                        ${ITEMS_PER_PAGE_OPTIONS.map(opt => `
                            <option value="${opt}" ${this.itemsPerPage === opt ? 'selected' : ''}>${opt}</option>
                        `).join('')}
                    </select>
                    <span class="text-sm text-gray-400">
                        ${(this.currentPage - 1) * this.itemsPerPage + 1}–${Math.min(this.currentPage * this.itemsPerPage, this.filteredLoads.length)} из ${this.filteredLoads.length}
                    </span>
                </div>
                <div class="flex items-center gap-2">
                    <button onclick="cargoManager.setPage(${this.currentPage - 1})" 
                        ${this.currentPage === 1 ? 'disabled' : ''} 
                        class="px-3 py-1 bg-dark-border rounded text-sm disabled:opacity-50 disabled:cursor-not-allowed">
                        ←
                    </button>
                    <span class="text-sm">
                        Страница ${this.currentPage} из ${totalPages}
                    </span>
                    <button onclick="cargoManager.setPage(${this.currentPage + 1})" 
                        ${this.currentPage === totalPages ? 'disabled' : ''} 
                        class="px-3 py-1 bg-dark-border rounded text-sm disabled:opacity-50 disabled:cursor-not-allowed">
                        →
                    </button>
                </div>
            </div>
        `;
        
        container.innerHTML = paginationHTML;
    }
}

// Экспортируем для использования в HTML
window.CargoManager = CargoManager;


