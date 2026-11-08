"""Apply concurrent extraction results in transcript order without blocking speech."""


class OrderedResults:
    def __init__(self):
        self.issued = 0
        self.next = 0
        self.ready = {}

    def reserve(self):
        ticket = self.issued
        self.issued += 1
        return ticket

    def complete(self, ticket, result):
        if ticket < self.next or ticket in self.ready:
            return []
        self.ready[ticket] = result
        available = []
        while self.next in self.ready:
            available.append(self.ready.pop(self.next))
            self.next += 1
        return available
