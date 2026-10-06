from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import Table
from docx.shared import Cm


SOURCE = Path(r"C:\Users\MAXIM\Desktop\универ\МатМоделирование Терехов.docx")
OUTPUT = Path(r"C:\Users\MAXIM\Downloads\торжок бологое\vehicle-route-pc\МатМоделирование_Терехов_дополнено.docx")


def paragraph_from_template(template, text):
    paragraph_xml = OxmlElement("w:p")
    if template._p.pPr is not None:
        paragraph_xml.append(deepcopy(template._p.pPr))

    run_xml = OxmlElement("w:r")
    if template.runs and template.runs[0]._r.rPr is not None:
        run_xml.append(deepcopy(template.runs[0]._r.rPr))
    text_xml = OxmlElement("w:t")
    if text.startswith(" ") or text.endswith(" "):
        text_xml.set(qn("xml:space"), "preserve")
    text_xml.text = text
    run_xml.append(text_xml)
    paragraph_xml.append(run_xml)
    return paragraph_xml


def set_cell_text(cell, text):
    paragraph = cell.paragraphs[0]
    template_run = paragraph.runs[0] if paragraph.runs else None
    for child in list(paragraph._p):
        if child.tag != qn("w:pPr"):
            paragraph._p.remove(child)
    run = paragraph.add_run(text)
    if template_run is not None and template_run._r.rPr is not None:
        run._r.insert(0, deepcopy(template_run._r.rPr))


doc = Document(SOURCE)
anchor = doc.paragraphs[61]
section_heading = doc.paragraphs[61]
subheading = doc.paragraphs[48]
body = doc.paragraphs[60]

elements = []


def add_paragraph(template, text):
    element = paragraph_from_template(template, text)
    if template is subheading:
        paragraph_properties = element.find(qn("w:pPr"))
        spacing = paragraph_properties.find(qn("w:spacing"))
        if spacing is None:
            spacing = OxmlElement("w:spacing")
            paragraph_properties.append(spacing)
        spacing.set(qn("w:before"), "120")
        spacing.set(qn("w:after"), "60")
    elements.append(element)


add_paragraph(section_heading, "5 Восстановление пропущенных строк")
add_paragraph(subheading, "Постановка задачи")
add_paragraph(
    body,
    "В route.csv отсутствовали 127 последовательных записей в трёх участках маршрута. "
    "Восстановление выполнялось только по сохранившимся строкам и структуре модели. "
    "Значения удалённых строк из резервных или встроенных копий не использовались. "
    "Точное возвращение исходных измерений при таком условии невозможно, поэтому результат представляет расчётную реконструкцию профиля.",
)

table_xml = deepcopy(doc.tables[2]._tbl)
table = Table(table_xml, doc.tables[2]._parent)
while len(table.rows) > 5:
    table._tbl.remove(table.rows[-1]._tr)
table.autofit = False
column_widths = [Cm(6.5), Cm(5.5), Cm(4.5)]
grid_columns = table._tbl.tblGrid.gridCol_lst
for grid_column, width in zip(grid_columns, column_widths):
    grid_column.set(qn("w:w"), str(width.twips))
for row in table.rows:
    for cell, width in zip(row.cells, column_widths):
        cell.width = width
table_data = [
    ["Участок маршрута", "Номера точек", "Число строк"],
    ["35,75-42,75 км", "143-171", "29"],
    ["72,75-84,75 км", "291-339", "49"],
    ["106,25-118,25 км", "425-473", "49"],
    ["Итого", "", "127"],
]
for row, values in zip(table.rows, table_data):
    for cell, value in zip(row.cells, values):
        set_cell_text(cell, value)
elements.append(table_xml)

add_paragraph(subheading, "Определение повреждённых участков")
add_paragraph(
    body,
    "Целостность проверялась по полям point_id и distance_m. В исправной части файла номера точек возрастают на единицу, "
    "а расстояние увеличивается на 250 м. Сопоставление с vehicle.csv подтвердило наличие всех 500 идентификаторов и "
    "позволило восстановить сетку расстояний для пропущенных строк. При этом значения координат, высот и уклонов из "
    "vehicle.csv получить нельзя, поэтому они рассчитывались отдельно.",
)

add_paragraph(subheading, "Восстановление траектории")
add_paragraph(
    body,
    "Для каждого разрыва использовались две последние доступные координаты перед ним и две первые координаты после него. "
    "По этим парам оценивались направления входа и выхода. Широта и долгота переводились в локальную систему координат в "
    "метрах, после чего граничные точки соединялись кубической кривой Безье. Длина управляющих векторов подбиралась "
    "численно так, чтобы длина кривой совпала с разностью distance_m на границах разрыва.",
)
add_paragraph(
    body,
    "Полученная кривая повторно разбивалась по длине дуги с шагом 250 м. Такой подход сохраняет непрерывность направления "
    "и не заменяет многокилометровый участок прямым отрезком. После обратного преобразования в широту и долготу средний "
    "геометрический шаг восстановленных точек составил около 250 м.",
)

add_paragraph(subheading, "Восстановление высоты")
add_paragraph(
    body,
    "Поле raw_srtm_elevation_m восстанавливалось как функция расстояния методом PCHIP. Это кусочная кубическая "
    "интерполяция Эрмита, сохраняющая форму данных и не создающая лишних экстремумов между соседними опорными точками. "
    "Затем к восстановленному ряду применялось используемое в проекте сглаживание по пяти точкам с весами 1, 2, 3, 2, 1 "
    "и делением суммы на 9. Результат записывался в elevation_m.",
)

add_paragraph(subheading, "Расчёт зависимых полей")
add_paragraph(
    body,
    "Поля grade_pct, heading_deg и curve_radius_m не интерполировались независимо. Уклон вычислялся по изменению "
    "сглаженной высоты между соседними точками и выражался в процентах. Направление определялось по приращениям широты и "
    "долготы с поправкой на среднюю широту. Радиус поворота рассчитывался по изменению направления на соседних отрезках; "
    "как и в генераторе проекта, применялось нижнее ограничение 50 м.",
)

add_paragraph(subheading, "Повторное обучение прогноза")
add_paragraph(
    body,
    "Чтобы прогноз уклона не зависел от удалённых исходных значений, гребневая регрессия обучалась заново. Из обучающей "
    "выборки исключались точки, для которых текущие или целевые значения попадали в повреждённые диапазоны. Осталось 349 "
    "допустимых примеров. Для контрольной проверки 242 примера использовались при обучении и 105 при последовательной "
    "отложенной проверке. Средняя абсолютная ошибка составила 0,305 процентного пункта, а ошибка базового прогноза — "
    "0,569 процентного пункта.",
)

add_paragraph(subheading, "Проверка результата")
add_paragraph(
    body,
    "После реконструкции route.csv содержит 500 строк с идентификаторами от 0 до 499. Идентификаторы и distance_m "
    "совпадают с vehicle.csv, расстояния строго возрастают, а обязательные числовые поля заполнены. Геометрический шаг "
    "на восстановленных участках находится в диапазоне от 249,88 до 250,19 м. Встроенный набор в data.js и параметры "
    "модели в model_report.json обновлены в соответствии с восстановленным профилем.",
)

add_paragraph(subheading, "Ограничения восстановления")
add_paragraph(
    body,
    "Реконструкция обеспечивает непрерывный и согласованный вход для учебного симулятора, но не является доказательством "
    "фактической формы дороги внутри разрывов. Одни и те же граничные точки допускают несколько вариантов траектории и "
    "профиля высоты. Для точного восстановления потребовалось бы повторно получить геометрию OSRM и высоты SRTM либо "
    "использовать независимую резервную копию измерений.",
)

for element in elements:
    anchor._p.addprevious(element)

doc.save(OUTPUT)
print(OUTPUT)
