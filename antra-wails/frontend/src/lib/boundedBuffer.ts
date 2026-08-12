export class BoundedDeque<T> {
  private values: Array<T | undefined>;
  private start = 0;
  private count = 0;

  constructor(private capacity: number) {
    this.capacity = Math.max(1, Math.floor(capacity));
    this.values = new Array(this.capacity);
  }

  get length(): number {
    return this.count;
  }

  clear(): void {
    this.values = new Array(this.capacity);
    this.start = 0;
    this.count = 0;
  }

  setCapacity(capacity: number): void {
    const nextCapacity = Math.max(1, Math.floor(capacity));
    if (nextCapacity === this.capacity) return;
    const retained = this.toArray().slice(0, nextCapacity);
    this.capacity = nextCapacity;
    this.values = new Array(this.capacity);
    this.start = 0;
    this.count = 0;
    retained.forEach(value => this.pushBack(value));
  }

  replace(values: readonly T[]): void {
    this.clear();
    values.slice(0, this.capacity).forEach(value => this.pushBack(value));
  }

  pushBack(value: T): void {
    if (this.count < this.capacity) {
      this.values[(this.start + this.count) % this.capacity] = value;
      this.count += 1;
      return;
    }
    this.values[this.start] = value;
    this.start = (this.start + 1) % this.capacity;
  }

  pushFront(value: T): void {
    this.start = (this.start - 1 + this.capacity) % this.capacity;
    this.values[this.start] = value;
    if (this.count < this.capacity) this.count += 1;
  }

  toArray(): T[] {
    const result: T[] = [];
    for (let index = 0; index < this.count; index += 1) {
      result.push(this.values[(this.start + index) % this.capacity] as T);
    }
    return result;
  }
}
