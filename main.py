import requests
import datetime
import csv
import pygame
from bs4 import BeautifulSoup
from pygame.locals import *
import math
import random
from collections import defaultdict
from collections import deque
import time
import copy
import numpy as np

toCSV = False
# stocks = ['AI', 'TSLA', 'AAPL', 'MSFT', 'UBS', 'BAIDF', 'GOOGL', 'AMZN', 'BRK-B', 'JNJ', 'XOM', 'WMT', 'JPM', 'PG', 'NVDA',
#         'CVX', 'LLY', 'KO', 'DIS', 'MCD', 'HON', 'DE', 'F']
stocks = ['TSLA', 'AAPL', 'XOM', 'LLY', 'MCD', 'NVDA']
showMAs = False
MAPeriods = [100, 50]
MACD = True
visualizer = True
backTest = True
evalPeriod = 10

stockData = {}
allDates = set()
currentDate = round(time.time())

def clamp(n, minimum, maximum):
    return max(minimum, min(n, maximum))


for symbol in stocks:
    # Goes to first page to find the start and end timestamps of the stock's lifetime; can't access range=max directly
    # because it is locked at 3mo intervals for whatever fucking reason
    page1 = requests.get(f'https://query1.finance.yahoo.com/v7/finance/chart/{symbol}?range=max&interval=1d&indicators=quote&includeTimestamps=true', stream=True, headers={'User-agent': 'Mozilla/5.0'})
    soup = BeautifulSoup(page1.content, "html.parser").string
    a = soup.find('firstTradeDate')
    b = soup.find('regularMarketTime')
    c = soup.find('gmtoffset')
    start = soup[a + len("firstTradeDate\":") : b - 2]
    end = soup[b + len("regularMarketTime\":") : c - 2]

    # Access page with all the actual data
    page2 = requests.get(f'https://query1.finance.yahoo.com/v7/finance/chart/{symbol}?period1={start}&period2={end}&interval=1d&events=history&includeAdjustedClose=true', stream=True, headers={'User-agent': 'Mozilla/5.0'})
    rawData = BeautifulSoup(page2.content, "html.parser").string

    # Uses tags to find where in the page the data is
    tags = ['\"timestamp\"', '\"open\"', '\"high\"', '\"low\"', '\"close\"', '\"adjclose\"', '\"volume\"']
    values = {}
    for tag in tags:
        # Goes to tag
        a = rawData.rindex(tag)
        data = rawData[a:]

        # Isolates relevant data based on location of brackets
        start = data.find('[') + 1
        end = data.find(']')
        relevantData = data[start : end]
        parsedData = relevantData.split(',')

        # Converts values from string to float
        column = []
        if tag == '\"timestamp\"':
            values['datetime'] = []
            for value in parsedData:
                newValue = currentDate if value is parsedData[-1] else int(value)
                column.append(newValue)
                allDates.add(newValue)

                date = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=newValue) if newValue < 0 else datetime.datetime.utcfromtimestamp(newValue)
                values['datetime'].append(date.strftime('%Y-%m-%d')) # Converts from Unix time to datetime and adds them to values
        else:
            for value in parsedData:
                column.append(float(value))

        # Adds data to values dict
        values[tag[1 : -1]] = column

    # Arranges values horizontally, with the Unix timestamp as the head
    finalValues = defaultdict(lambda: 0, {})
    MAQs = {}
    MACQ = []
    for period in MAPeriods:
        MAQs[period] = [0, deque()] # Sum, queue
    for i, timestamp in enumerate(values['timestamp']):
        row = {}

        # Adds normal tags to new data structure
        for tag, value in values.items():
            if tag != 'timestamp':
                row[tag] = value[i]

        # Calculate simple moving averages  
        for period, MAQ in MAQs.items():
            MAQ[0] += row['close']
            MAQ[1].append(row['close'])
            if i + 1 >= period:
                row[period] = MAQ[0] / period  # Divides sum to get mean and then subtracts the next element from the queue
                MAQ[0] -= MAQ[1].popleft()      # this is literally so genius

        # Calculate MACD
        if i == 0:
            MACQ = [row['close']] * 3 # Creates the MACQ here
        else:
            periods = [12, 26]
            k = lambda a : 2 / (a + 1)
            for iterator, period in enumerate(periods):
                MACQ[iterator] = row['close']*k(period) + MACQ[iterator]*(1 - k(period))
            MACQ[2] = (MACQ[0] - MACQ[1])*k(9) + MACQ[2]*(1 - k(9))
            if i + 1 > 26:
                row['MACD'] = MACQ[0] - MACQ[1]
                row['Signal'] = MACQ[2]

        finalDate = []
        finalValues[timestamp] = row

    # Writes data to internal dict
    stockData[symbol] = finalValues

    # Writes data to csv file
    if toCSV:
        with open(f'{symbol}.csv', 'w', newline='') as dataFile:
            writer = csv.writer(dataFile, delimiter=',', quotechar='"', quoting=csv.QUOTE_NONNUMERIC)

            # Writes header
            writeRow = ['Unix Date', 'Datetime', 'Open', 'High', 'Low', 'Close', 'Adjusted Close', 'Volume']
            writer.writerow(writeRow)

            # Writes by column instead of rows
            for unixDate, row in finalValues.items():
                writeRow = [unixDate]
                for value in row.values():
                    writeRow.append(value)

                writer.writerow(writeRow)

# Back tests various trading algorithms and exports returns over different periods into a csv
if backTest:
    with open('Backtest.csv', 'w', newline='') as dataFile, open('Backtest Transactions.csv', 'w', newline='') as transactionFile:
        # One file for returns and one file for # of transactions each algorithm used
        writer = csv.writer(dataFile, delimiter=',', quotechar='"', quoting=csv.QUOTE_NONNUMERIC)
        writerTransact = csv.writer(transactionFile, delimiter=',', quotechar='"', quoting=csv.QUOTE_NONNUMERIC)

        for symbol in stocks:
            # Writes header
            row = [*range(1, evalPeriod + 1)]
            row.insert(0, symbol)
            writer.writerow(row + ['Action'])
            writerTransact.writerow(row)

            stock = stockData[symbol]
            yTS = 60*60*24*365 # Years to seconds

            endDate = [*stock.keys()][-1]
            output = []
            outputTransact = []


            def writeName(name):
                output.append([name])
                outputTransact.append([name])


            def template(t, buyMasks, sellMasks, condition, offset, k):
                holding = False
                cash = 0
                minCash = 0
                transactions = 0
                action = ''
                for i, value in enumerate(values):
                    action = 'Hold'
                    if (not offset and condition[i]) or (offset and i != 0 and condition[
                        i - 1]):  # Represents a change in condition; ie. lines crossing
                        buy = True
                        for mask in buyMasks:
                            if not mask[i]:
                                buy = False
                                break
                        if buy:
                            action = 'Buy'
                            if not holding:
                                cash -= value['close']
                                holding = True
                                minCash = min(cash, minCash)
                                transactions += 1
                    else:
                        sell = True
                        for mask in sellMasks:
                            if not mask[i]:
                                sell = False
                                break
                        if sell:
                            action = 'Sell'
                            if holding:
                                cash += value['close']
                                holding = False
                if holding:
                    cash += values[-1]['close'] * holding

                if t == 'Action':
                    output[k].append(action)
                else:
                    output[k].append(f'{round((cash / -minCash + 1) ** (1 / t) * 100 - 100, 2)}%' if transactions != 0 else '0.00%')
                    outputTransact[k].append(transactions)

            for t in list(range(1, evalPeriod + 1)) + ['Action']:
                startDate = endDate - yTS if t == 'Action' else endDate - t*yTS

                n = 100
                m = 50
                dates = [k for k, v in stock.items() if startDate <= k <= endDate if 'Signal' in v and n in v]
                if not t == 'Action' and (dates[-1] - dates[0]) / yTS < t - 1: # If adding another year doesn't provide any new data points to the back tester
                    for value in output:
                        value.append('')
                    continue

                values = [v for k, v in stock.items() if startDate <= k <= endDate if 'Signal' in v and n in v]
                signals = np.array([d['Signal'] for d in values])
                MACDs = np.array([d['MACD'] for d in values])
                close = np.array([d['close'] for d in values])
                ns = np.array([d[n] for d in values])
                ms = np.array([d[m] for d in values])
                i = 0

                if t == 1:
                    writeName('MACD')
                buyMasks = [signals < MACDs]
                sellMasks = [signals > MACDs]
                template(t, buyMasks, sellMasks, signals > MACDs, True, i)
                i += 1

                if n in MAPeriods:
                    if t == 1:
                        writeName(f'MACD and {n}-day MA')
                    buyMasks = [signals < MACDs, close > ns]
                    sellMasks = [signals > MACDs, close < ns]
                    template(t, buyMasks, sellMasks, signals > MACDs, True, i)
                    i += 1

                if n in MAPeriods and m in MAPeriods:
                    if t == 1:
                        writeName(f'{n}-day MA and {m}-day MA')
                    buyMasks = [ns < ms]
                    sellMasks = [ns > ms]
                    template(t, buyMasks, sellMasks, ns > ms, True, i)
                    i += 1

                if t == 1:
                    writeName('Buy and Hold')
                buyMasks = []
                sellMasks = [[False]*len(values)]
                template(t, buyMasks, sellMasks, [True]*len(values), True, i)
                i += 1

                if t == 1:
                    writeName('Predictive MACD')
                effectiveSignal = signals*2 - np.insert(signals[:-1], 0, 0)
                effectiveMACD = MACDs*2 - np.insert(MACDs[:-1], 0, 0)
                buyMasks = [effectiveSignal < effectiveMACD]
                sellMasks = [effectiveSignal > effectiveMACD]
                template(t, buyMasks, sellMasks, effectiveSignal > effectiveMACD, True, i)
                i += 1

                if t == 1:
                    writeName('2-day Predictive MACD')
                effectiveSignal = signals*3 - np.insert(signals[:-2], 0, [0, 0])*2
                effectiveMACD = MACDs*3 - np.insert(MACDs[:-2], 0, [0, 0])*2
                buyMasks = [effectiveSignal < effectiveMACD]
                sellMasks = [effectiveSignal > effectiveMACD]
                template(t, buyMasks, sellMasks, effectiveSignal > effectiveMACD, True, i)
                i += 1

                if n in MAPeriods:
                    if t == 1:
                        writeName(f'Predictive MACD and {n}-day MA')
                    effectiveSignal = signals*2 - np.insert(signals[:-1], 0, 0)
                    effectiveMACD = MACDs*2 - np.insert(MACDs[:-1], 0, 0)
                    buyMasks = [effectiveSignal < effectiveMACD, close > ns]
                    sellMasks = [effectiveSignal > effectiveMACD, close < ns]
                    template(t, buyMasks, sellMasks, effectiveSignal > effectiveMACD, True, i)
                    i += 1

            for row in output:
                writer.writerow(row)
            writer.writerow([])
            for row in outputTransact:
                writerTransact.writerow(row)
            writerTransact.writerow([])

if visualizer:
    # Setup
    pygame.init()
    windowDim = (800, 800)
    window = pygame.display.set_mode(windowDim, pygame.RESIZABLE)
    clock = pygame.time.Clock()
    running = True
    updateData = True
    updateDisplay = True
    allDates = list(allDates)
    allDates.sort()

    font = pygame.font.SysFont('trebuchetms', 14)
    MARGIN = 50
    colors = {}
    for key in stockData:
        r = random.randrange(100, 255)
        g = random.randrange(100, 255)
        b = random.randrange(100, 255)
        color = (r, g, b)
        colors[key] = color

    # Finds minimum and maximum dates
    minDate = allDates[0]
    maxDate = allDates[-1]
    dateRange = maxDate - minDate

    zoom = 1 # >= 1
    zoomOffset = 0 # Represents how far the displayed part of the graph is offset in terms of percentage of the window; > 0 and < (1 - zoom)
    mouseXOld = 0

    # Main loop
    while running:
        clock.tick(60)

        # Handles user input
        mouseX, mouseY = pygame.mouse.get_pos()
        keyEvents = pygame.key.get_pressed()
        mouseEvents = pygame.mouse.get_pressed()
        for event in pygame.event.get():
            if event.type == QUIT:
                running = False
                break

            if event.type == pygame.VIDEORESIZE:
                windowDim = (event.w, event.h)
                updateDisplay = True

            if event.type == pygame.MOUSEBUTTONDOWN:
                if mouseX > 0 and mouseX < windowDim[0] and mouseY > 0 and mouseY < windowDim[1]:
                    if event.button == 1: # Scrolling
                        mouseXOld = mouseX
                    else:
                        k = 1.1
                        mouseRelative = mouseX/windowDim[0]
                        if event.button == 4: # Zooming in
                            zoom *= k
                            zoomOffset = clamp(zoomOffset*k + (k - 1) * mouseRelative, 0, zoom - 1) # theres some mathemagics goin on here
                            updateData = True
                        elif event.button == 5 and not zoom == 1: # Zooming out
                            zoom = max(1, zoom/k)
                            zoomOffset = clamp((zoomOffset - (k - 1) * mouseRelative) / k, 0, zoom - 1)
                            updateData = True

        if keyEvents[K_ESCAPE]:
            running = False
            break
        if mouseEvents[0]:
            mouseDiff = mouseX - mouseXOld
            if mouseDiff:
                zoomOffset = clamp(zoomOffset - mouseDiff/windowDim[0], 0, zoom - 1)
                mouseXOld = mouseX
                updateData = True


        # When display bounds are changed, change min/max prices and date
        if updateData:
            startTime = time.perf_counter()

            startIndex = math.ceil(zoomOffset / zoom * (len(allDates) - 1))
            indexDiff = math.ceil(1 / zoom * (len(allDates) - 1))
            endIndex = startIndex + indexDiff

            allDisplayedDates = allDates[startIndex : endIndex + 1]

            # Price range
            priceList = []
            for stock in stockData.values():
                for date in allDisplayedDates:
                    values = stock[date]
                    if values:
                        priceList.append(values['close'])
                        for period in MAPeriods:
                            if period in values:
                                priceList.append(values[period])
            startPrice = min(priceList)
            endPrice = max(priceList)
            priceDiff = endPrice - startPrice

            # MACD range
            if MACD:
                stock = stockData[stocks[0]]
                priceList = []
                for date in allDisplayedDates:
                    values = stock[date]
                    if values and 'MACD' in values:
                        priceList.extend([values['MACD'], values['Signal']])
                startMACD = min(priceList) if priceList else 0
                endMACD = max(priceList) if priceList else 0
                MACTechnology = endMACD - startMACD

            updateData = False
            updateDisplay = True
            #print(f'Update time: {time.perf_counter() - startTime}')


        if updateDisplay:
            startTime = time.perf_counter()
            graph = pygame.Surface(windowDim, pygame.SRCALPHA)

            # Creates list of dates that will be displayed on the graph
            displayedDates = []
            if indexDiff > windowDim[0]: # If more price values than width pixels...
                increment = (indexDiff - 1) / (windowDim[0] - 1) # = the difference between each date
                for i in range(windowDim[0]): # Iterates and adds the closest date that would correspond to each pixel
                    index = i*increment
                    displayedDates.append(allDisplayedDates[round(index)])
                xScale = 1
            else:
                displayedDates = allDisplayedDates
                xScale = windowDim[0] / (len(displayedDates) - 1)

            dataLookups = {}
            if MACD:
                MACDSize = MARGIN*3
                yScale = (windowDim[1] - MARGIN - MACDSize) / priceDiff # Used to scale Yvalues
                yStart = windowDim[1] - MACDSize
                if MACTechnology:
                    MACDScale = MACDSize / MACTechnology
                    MACDMid = windowDim[1] - round(MACDSize/2)
            else:
                yScale = (windowDim[1] - MARGIN*2) / priceDiff
                yStart = windowDim[1] - MARGIN

            for symbol, stock in stockData.items():
                # Setup data structures
                pointPrevious = deque()
                MAsPrevious = deque()
                dataLookup = {}

                # Draws points to a surface
                iterator = 0
                for i, date in enumerate(displayedDates):
                    value = stock[date]
                    if value:
                        # Adds normal points to point queue and copies relevant data to dataLookups
                        price = value['close']
                        point = (i * xScale, yStart - math.ceil((price - startPrice) * yScale))
                        pointPrevious.append(point)

                        # Adds moving average points to the moving average queue
                        MAPoint = {}
                        if showMAs:
                            for period in MAPeriods:
                                if period in value:
                                    MAPoint[period] = (i * xScale,  yStart - math.ceil((value[period] - startPrice) * yScale))
                        if MACD and symbol == stocks[0] and 'MACD' in value and MACTechnology:
                            MAPoint['MACD'] = (i * xScale, windowDim[1] - math.ceil((value['MACD'] - startMACD) * MACDScale))
                            MAPoint['Signal'] = (i * xScale, windowDim[1] - math.ceil((value['Signal'] - startMACD) * MACDScale))
                            MACDifference = MAPoint['MACD'][1] - MAPoint['Signal'][1]
                            pygame.draw.line(graph, (255, 0, 0) if MACDifference >= 0 else (0, 255, 0), (i * xScale, MACDMid), (i * xScale, MACDMid + MACDifference))
                        MAsPrevious.append(MAPoint)

                        # Draws points to surface
                        if len(pointPrevious) > 1:
                            MAPrevious = MAsPrevious.popleft()
                            commonKeys = MAPoint.keys() & MAPrevious.keys()
                            for key in commonKeys:
                                pygame.draw.line(graph, (200, 100, 100), MAPoint[key], MAPrevious[key], width=2)
                            pygame.draw.line(graph, colors[symbol], point, pointPrevious.popleft(), width=2)

                        # Add lookup point
                        dataLookup[i * xScale] = {
                            'Pos' : point,
                            'Price' : price,
                            'Date' : value['datetime']
                            }

                if MACD:
                    pygame.draw.line(graph, (255,255,255), (0, yStart), (windowDim[0], yStart))

                # Saves displayed points to later call upon when finding specific stock prices and dates
                dataLookups[symbol] = dataLookup

            updateDisplay = False
            #print(f'Display Time: {time.perf_counter() - startTime}')


        # Drawing
        window.fill((0, 0, 0))

        # Displays stock price and date if mouse is hovering over a line
        if mouseX > 0 and mouseX < windowDim[0] and mouseY > 0 and mouseY < windowDim[1]:
            if len(allDisplayedDates) >= windowDim[0]:
                lookupX = mouseX
            else:
                lookupX = round(mouseX / xScale) * xScale

            highlightPoints = []
            # For each stock, find if there is a point with the same xval as mouse and adds them to a list
            for symbol, dataLookup in dataLookups.items():
                if lookupX in dataLookup:
                    newPoint = copy.deepcopy(dataLookup[lookupX])
                    newPoint['Symbol'] = symbol
                    highlightPoints.append(newPoint)
            if highlightPoints:
                closestPoint = min(highlightPoints, key = lambda point : abs(point['Pos'][1] - mouseY))

                pygame.draw.line(window, (50, 50, 50), (lookupX, 0), (lookupX, windowDim[1]))
                pygame.draw.line(window, (50, 50, 50), (0, closestPoint['Pos'][1]), (windowDim[0], closestPoint['Pos'][1]))

                textSurf = font.render(f'{closestPoint["Symbol"]} : {closestPoint["Date"]} : {round(closestPoint["Price"], 2)}', True, (255, 255, 255))
                window.blit(textSurf, (clamp(closestPoint['Pos'][0], 0, windowDim[0] - textSurf.get_width()), closestPoint['Pos'][1]))

        window.blit(graph, (0, 0))

        # Updates display screen
        pygame.display.flip()