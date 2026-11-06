"""Regenerate tiny synthetic documents/protocol fixtures; no external model call.

The reference answers below were keyed for the fictional orders. Deliberately
wrong proposals exercise review. They are NOT captures from a paid model.
"""
import copy
from email.message import EmailMessage
import json
from pathlib import Path

from reportlab.pdfgen import canvas
from erp_demo.intake import load_document
from erp_demo.models import source_hash

ROOT = Path(__file__).resolve().parent


def field(value, page=1, confidence=.99):
    return {'value': value, 'page': page, 'text': value, 'confidence': confidence}


def answer(po, lines, customer='SYN-CUSTOMER'):
    return {'customer': customer, 'purchase_order': po, 'currency': 'CAD', 'lines': lines}


def proposal(label, line_pages=None):
    result = {key: field(label[key]) for key in ('customer', 'purchase_order', 'currency')}
    result['lines'] = [{key: field(value, (line_pages or [1]*len(label['lines']))[i]) for key,value in row.items()}
                       for i,row in enumerate(label['lines'])]
    return result


def text(label):
    return '\n'.join(['SYNTHETIC CUSTOMER PURCHASE ORDER', 'Customer: '+label['customer'],
                      'PO: '+label['purchase_order'], 'Currency: CAD', 'Item | Quantity | Unit price',
                      *[' | '.join(row.values()) for row in label['lines']]])


def email(path, body, attachment=None):
    msg = EmailMessage()
    msg['From'] = 'synthetic@example.invalid'
    msg['To'] = 'workshop@example.invalid'
    msg['Subject'] = 'Synthetic purchase order for testing'
    msg.set_content(body)
    if attachment:
        msg.add_attachment(attachment.read_bytes(), maintype='application', subtype='pdf', filename=attachment.name)
    path.write_bytes(msg.as_bytes())


def main():
    lotion = dict(item='FG-LOTION', quantity='2', unit_price='12.50')
    balm = dict(item='FG-BALM', quantity='1', unit_price='15.00')
    labels = []
    for name, label, status in [
        ('clean', answer('SYN-PO-1',[lotion]), 'ready'),
        ('unknown', answer('SYN-PO-2',[dict(item='FG-UNKNOWN',quantity='2',unit_price='12.50')]), 'review'),
        ('price', answer('SYN-PO-3',[dict(item='FG-LOTION',quantity='2',unit_price='11.00')]), 'review'),
        ('weak', answer('SYN-PO-4',[lotion]), 'review'),
        ('invented', answer('SYN-PO-5',[lotion]), 'review'),
        ('omitted', answer('SYN-PO-6',[lotion,balm]), 'review')]:
        email(ROOT/(name+'.eml'), text(label))
        predicted = proposal(label)
        if name == 'weak': predicted['lines'][0]['quantity']['confidence'] = .35
        if name == 'invented': predicted['lines'][0]['quantity']['value'] = '200'
        if name == 'omitted': predicted['lines'].pop()
        labels.append((name, name+'.eml',label,predicted,status))
    label = answer('SYN-PO-7',[lotion,balm])
    pdf = ROOT/'two-page.pdf'
    drawing = canvas.Canvas(str(pdf), invariant=True, pagesize=(612,792))
    for page, content in enumerate([text(answer('SYN-PO-7',[lotion])),
                                     'SYNTHETIC PURCHASE ORDER - page 2\nFG-BALM | 1 | 15.00'], 1):
        drawing.setFont('Helvetica', 12)
        for i,line in enumerate(content.splitlines()): drawing.drawString(54, 738-i*22,line)
        drawing.drawString(54, 54, f'Page {page} - generated demo data')
        drawing.showPage()
    drawing.save()
    labels.append(('two-page','two-page.pdf',label,proposal(label,[1,2]),'ready'))
    email(ROOT/'attachment.eml','Synthetic order attached. No instructions to execute.',pdf)
    predicted = proposal(label,[2,3])
    for key in ('customer','purchase_order','currency'): predicted[key]['page'] = 2
    labels.append(('attachment','attachment.eml',label,predicted,'ready'))
    keyed = []
    for i,(name,file,label,predicted,status) in enumerate(labels):
        backend = 'anthropic' if i%2 == 0 else 'ollama'
        response = ({'content':[{'type':'tool_use','id':'synthetic_'+name,'name':'extract_order','input':predicted}]}
                    if backend == 'anthropic' else
                    {'message':{'role':'assistant','tool_calls':[{'function':{'name':'extract_order','arguments':predicted}}]}})
        recording = dict(provenance='Hand-authored synthetic protocol fixture; not a live model capture', backend=backend,
                         source_sha256=source_hash(load_document(ROOT/file)),response=response)
        (ROOT/'recordings'/(name+'.json')).write_text(json.dumps(recording,indent=2)+'\n')
        keyed.append(dict(id=name,file=file,reference=copy.deepcopy(label),expected_status=status, expected_document_status='review' if name in ('unknown','price') else 'ready'))
    (ROOT/'labels.json').write_text(json.dumps(keyed,indent=2)+'\n')


if __name__ == '__main__': main()
