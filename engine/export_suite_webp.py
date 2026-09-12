import re

def emphasized(entry):
    return bool(re.search(r'collaps|fall|lift|carr(?:y|ies)|support|cry|sob|hug|tongue',entry['description'],re.I))

def rectangles(entries):
    rows=[];index=0
    while index<len(entries):
        compact=lambda e: e['camera']['framing'] in {'portrait','close-up','upper body'} and not emphasized(e)
        if index+1<len(entries) and compact(entries[index]) and compact(entries[index+1]):
            rows.append([index,index+1]);index+=2
        else:rows.append([index]);index+=1
    weights=[.85 if len(row)==2 else 1.4 if emphasized(entries[row[0]]) else 1.0 for row in rows]
    usable=1-.025*(len(rows)+1);total=sum(weights);top=.025
    boxes={};labels=[]
    for row_index,(row,weight) in enumerate(zip(rows,weights)):
        height=usable*weight/total
        for column,number in enumerate(row):
            width=(1-.025*(len(row)+1))/len(row)
            left=.025+column*(width+.025)
            boxes[number]=[left,top,left+width,top+height]
        position='top' if row_index==0 else 'bottom' if row_index==len(rows)-1 else 'middle'
        labels.append(('two small '+position+' panels') if len(row)==2 else ('large ' if weight>1 else 'wide ')+position+' panel')
        top+=height+.025
    return boxes,', '.join(labels)
